"""Shared scaffolding for the WI-00015 operator scripts.

The scripts keep their factories and helper names as module globals (tests
monkeypatch them there); everything here is stateless and receives those
collaborators as arguments.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .laser import FailureKind, ProcedureStatus
from ._procedure import ReportArtifactEvidence, ReportArtifactStatus


PROCEDURE_ID = "WI-00015"
APPROVED_PROCEDURE_REVISION = (
    "WI-00015 automated process addendum approved 2026-08-12"
)


@dataclass(frozen=True)
class OperatorRunReportRequest:
    """Report metadata, including the approved procedure revision."""

    request: object
    procedure_revision: str


class OperatorCanceled(Exception):
    pass


def make_parser(
    description: str | None,
    add_extra_arguments: Callable[[argparse.ArgumentParser], None] | None = None,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--output-dir", default="./wi15_out")
    parser.add_argument("--operator")
    parser.add_argument("--build-revision")
    parser.add_argument("--fixture-id")
    if add_extra_arguments is not None:
        add_extra_arguments(parser)
    parser.add_argument(
        "--procedure-revision", default=APPROVED_PROCEDURE_REVISION
    )
    return parser


def required_value(
    value: str | None, prompt: str, input_func: Callable[[str], str]
) -> str:
    while True:
        candidate = value if value is not None else input_func(prompt)
        value = None
        candidate = candidate.strip()
        if candidate:
            return candidate


def confirmed(prompt: str, input_func: Callable[[str], str]) -> bool:
    return input_func(prompt).strip().lower() in ("yes", "y")


def utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def close_best_effort(resource) -> None:
    if resource is None:
        return
    try:
        resource.close()
    except Exception:
        pass


def close_bench_capturing(bench) -> str | None:
    """Close the bench, returning the failure text instead of raising."""
    try:
        bench.close()
    except Exception as exc:
        return str(exc) or exc.__class__.__name__
    return None


def apply_cleanup_failure(result, cleanup_failure: str | None, recorder):
    """Fold a bench-close failure into the terminal result and checkpoint it."""
    if cleanup_failure is None:
        return result
    was_passing = result.status is ProcedureStatus.PASSED
    result = replace(
        result,
        status=ProcedureStatus.FAILED if was_passing else result.status,
        failure_kind=(
            FailureKind.MEASUREMENT if was_passing else result.failure_kind
        ),
        failure_reason=(
            "Hardware resource cleanup failed."
            if was_passing
            else result.failure_reason
        ),
        resource_cleanup_failure=cleanup_failure,
    )
    recorder.checkpoint(result)
    return result


def finalize_run_artifacts(
    *,
    request,
    result,
    recorder,
    report_factory,
    procedure_revision: str,
    output_func: Callable[[str], None],
) -> int:
    """Persist incomplete/finalized report evidence and print the terminal lines.

    Every stage transition is checkpointed before the next fallible step so an
    interruption can never leave a claimed-but-missing artifact.
    """
    report_path = Path(recorder.run_directory) / "report.html"
    incomplete_result = replace(
        result,
        report_paths=(Path(recorder.json_path),),
        report_artifact=ReportArtifactEvidence(
            report_path, ReportArtifactStatus.INCOMPLETE
        ),
    )
    recorder.checkpoint(incomplete_result)
    try:
        report = report_factory(recorder.run_directory)
        report_path = Path(report.report_path)
        finalized_result = replace(
            incomplete_result,
            report_paths=(Path(recorder.json_path), report_path),
            report_artifact=ReportArtifactEvidence(
                report_path, ReportArtifactStatus.FINALIZED
            ),
        )
        written_report = report.write(
            OperatorRunReportRequest(request, procedure_revision),
            finalized_result,
            recorder.json_path,
        )
        if (
            Path(written_report).resolve() != report_path.resolve()
            or not report_path.is_file()
        ):
            raise RuntimeError("HTML report writer did not create the expected file")
    except Exception as exc:
        report_failure = str(exc) or exc.__class__.__name__
        was_passing = result.status is ProcedureStatus.PASSED
        failed_result = replace(
            incomplete_result,
            status=ProcedureStatus.FAILED if was_passing else result.status,
            failure_kind=(
                FailureKind.REPORT if was_passing else result.failure_kind
            ),
            failure_reason=(
                "HTML report generation failed."
                if was_passing
                else result.failure_reason
            ),
            report_paths=(Path(recorder.json_path),),
            report_artifact=ReportArtifactEvidence(
                report_path,
                ReportArtifactStatus.FAILED,
                report_failure,
            ),
        )
        recorder.checkpoint(failed_result)
        output_func(f"Could not create the report: {report_failure}")
        return 1
    recorder.checkpoint(finalized_result)
    output_func(f"Final result: {finalized_result.status.value}")
    if finalized_result.failure_kind is not None:
        output_func(f"Problem type: {finalized_result.failure_kind.value}")
    if finalized_result.failure_reason is not None:
        output_func(f"Problem: {finalized_result.failure_reason}")
    output_func(f"Saved data (JSON): {Path(recorder.json_path).resolve()}")
    output_func(f"Saved report (HTML): {Path(report_path).resolve()}")
    return 0 if finalized_result.status is ProcedureStatus.PASSED else 1
