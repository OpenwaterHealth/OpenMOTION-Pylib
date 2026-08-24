"""Shared scaffolding for the WI-00015 operator scripts.

The scripts keep their factories and helper names as module globals (tests
monkeypatch them there); everything here is stateless and receives those
collaborators as arguments.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .laser import (
    EnergyMeasurement,
    FailureKind,
    ProcedureStatus,
    SettingReadback,
)
from .reporting import _safe_component
from ._procedure import ReportArtifactEvidence, ReportArtifactStatus


PROCEDURE_ID = "WI-00015"
APPROVED_PROCEDURE_REVISION = (
    "WI-00015 automated process addendum approved 2026-08-12"
)

# Terminal-output convention shared with the test app's Procedures pane:
# operator lines are plain short sentences; engineer-facing narration is
# prefixed with DETAIL_PREFIX. The pane hides prefixed lines while its
# Verbose checkbox is off (display-only - scripts always emit everything, so
# the toggle can apply retroactively and headless runs see the full stream).
DETAIL_PREFIX = "# "


def emit_detail(output_func: Callable[[str], None], message: str) -> None:
    """Print an engineer-facing detail line (hidden when Verbose is off)."""
    output_func(f"{DETAIL_PREFIX}{message}")


class _DetailLogHandler(logging.StreamHandler):
    """Marker subclass so repeated forward_library_logging() calls are no-ops.

    Emits to whatever ``sys.stderr`` currently is, so stream replacement
    (pytest capture, runner reconfiguration) never leaves it holding a dead
    stream object.
    """

    def emit(self, record):
        self.stream = sys.stderr
        super().emit(record)


def forward_library_logging() -> None:
    """Route WARNING+ library log records to stderr as detail lines.

    The scripts run as children of the Procedures pane, which merges stderr
    into its terminal. Without a handler, Python's last-resort handler prints
    bare messages the pane cannot classify as narration; formatting them with
    DETAIL_PREFIX keeps them out of the plain operator view while preserving
    them for verbose reading and the pane's audit log.
    """
    root = logging.getLogger()
    if any(isinstance(handler, _DetailLogHandler) for handler in root.handlers):
        return
    handler = _DetailLogHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(f"{DETAIL_PREFIX}%(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)


class EventEchoRecorder:
    """Forward recorder calls unchanged; echo each event as a detail line.

    The workflows narrate themselves through ``ProcedureEvent`` records that
    normally reach only the JSON evidence file; echoing them gives the
    verbose terminal the same story at no cost to the evidence. The
    underlying recorder stays reachable as ``wrapped`` (tests assert wiring
    identity through it).
    """

    def __init__(self, recorder, output_func: Callable[[str], None]):
        self.wrapped = recorder
        self._output = output_func

    def __getattr__(self, name):
        return getattr(self.wrapped, name)

    def record(self, event) -> None:
        self.wrapped.record(event)
        message = getattr(event, "message", None)
        if message:
            self._output(f"{DETAIL_PREFIX}{message}")


def _format_value(value) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int | float):
        return f"{value:g}"
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return ", ".join(
            f"{key}={_format_value(item)}" for key, item in value.items()
        )
    return type(value).__name__


def _describe_result(value) -> str:
    if value is None:
        return "done"
    if isinstance(value, EnergyMeasurement):
        return (
            f"{value.n} pulses, mean {value.mean_uj:.1f} uJ, "
            f"stdev {value.stdev_uj:.1f} uJ, rate {value.rate_hz:.1f} Hz"
        )
    if isinstance(value, SettingReadback):
        return (
            f"requested {_format_value(value.requested)}, "
            f"read back {_format_value(value.actual)}"
        )
    return _format_value(value)


class BenchNarrator:
    """Wrap a bench so every hardware call narrates itself to the terminal.

    Each call prints one ``DETAIL_PREFIX`` line naming the call and a compact
    result summary, so the verbose view shows exactly what was set and read.
    Three kinds of always-visible operator lines are layered on top:

    * ``steps`` maps ``(method name, occurrence)`` to a plain line announcing
      the phase that call begins (e.g. the first ``measure_energy`` starts
      the measuring-and-adjusting phase).
    * Laser drive writes print what is being set, in units.
    * Energy measurements print the measured mean (and the target when one
      is supplied), which doubles as the not-hung heartbeat during tuning.

    Methods named in ``quiet`` are polled in tight loops; they narrate their
    first call only. The underlying bench stays reachable as ``wrapped``
    (tests assert wiring identity through it).
    """

    _SLOW_METHODS = frozenset(
        {
            "preflight",
            "preflight_dual",
            "preflight_console",
            "measure_energy",
            "power_cycle",
            "run_normal_scan",
            "bring_up_laser_configuration",
            "revalidate_topology",
            "revalidate_dual_topology",
            "close",
        }
    )

    def __init__(
        self,
        bench,
        output_func: Callable[[str], None],
        *,
        steps: Mapping[tuple[str, int], str] | None = None,
        quiet: tuple[str, ...] = (),
        target_energy_uj: float | None = None,
    ):
        self.wrapped = bench
        self._output = output_func
        self._steps = dict(steps or {})
        self._quiet = frozenset(quiet)
        self._target_energy_uj = target_energy_uj
        self._call_counts: dict[str, int] = {}

    def __getattr__(self, name):
        attribute = getattr(self.wrapped, name)
        if not callable(attribute):
            return attribute

        def narrated(*args, **kwargs):
            return self._narrated_call(name, attribute, args, kwargs)

        return narrated

    def _narrated_call(self, name, method, args, kwargs):
        count = self._call_counts.get(name, 0) + 1
        self._call_counts[name] = count
        step_line = self._steps.get((name, count))
        if step_line is not None:
            self._output(step_line)
        self._announce_intent(name, args)
        muted = name in self._quiet and count > 1
        described_args = ", ".join(
            [_format_value(arg) for arg in args]
            + [f"{key}={_format_value(item)}" for key, item in kwargs.items()]
        )
        if not muted and name in self._SLOW_METHODS:
            emit_detail(self._output, f"{name}({described_args}) ...")
        try:
            result = method(*args, **kwargs)
        except Exception as error:
            emit_detail(
                self._output,
                f"{name}({described_args}) failed: "
                f"{type(error).__name__}: {error}",
            )
            raise
        if not muted:
            emit_detail(
                self._output,
                f"{name}({described_args}) -> {_describe_result(result)}",
            )
        self._announce_result(name, result)
        return result

    def _announce_intent(self, name: str, args) -> None:
        if name != "write_register" or len(args) < 2:
            return
        register, value = args[0], args[1]
        if register == "TA_CURRENT_DRV":
            self._output(f"Setting the laser current to {value:g} mA ...")
        elif register == "TA_PULSE_WIDTH":
            self._output(f"Setting the laser pulse width to {value:g} us ...")

    def _announce_result(self, name: str, result) -> None:
        if name == "measure_energy" and isinstance(result, EnergyMeasurement):
            line = f"Measured {result.mean_uj:.0f} uJ."
            if self._target_energy_uj is not None:
                line += f" Target is {self._target_energy_uj:g} uJ."
            self._output(line)


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


def system_serial_number(result) -> str | None:
    """The console serial that identifies the system a run belongs to.

    Read from the run's own evidence - the safety result carries a top-level
    ``console_identity``; the laser results carry an ``identities`` tuple with
    the console entry in it. None when preflight never read a serial (e.g. the
    console never connected) or the console is unprogrammed.
    """
    console = getattr(result, "console_identity", None)
    identities = [console] if console is not None else []
    identities += list(getattr(result, "identities", ()) or ())
    for identity in identities:
        if getattr(identity, "role", None) == "console":
            serial = getattr(identity, "serial", None)
            if serial is not None and str(serial).strip():
                return str(serial).strip()
    return None


def run_artifact_stem(procedure_slug: str, result) -> str:
    """``<system-serial>-<slug>`` when the serial is known, else the slug.

    The file names carry what someone scanning a folder of runs needs - which
    unit and which procedure. The WI id and the exact time already live on the
    run directory and inside the report content, so they stay out of the file
    names.
    """
    serial = system_serial_number(result)
    slug = _safe_component(procedure_slug)
    return f"{_safe_component(serial)}-{slug}" if serial else slug


def finalize_run_artifacts(
    *,
    request,
    result,
    recorder,
    report_factory,
    procedure_revision: str,
    procedure_slug: str,
    output_func: Callable[[str], None],
) -> int:
    """Persist incomplete/finalized report evidence and print the terminal lines.

    Every stage transition is checkpointed before the next fallible step so an
    interruption can never leave a claimed-but-missing artifact. The run
    directory becomes ``<system-serial>-<procedure_slug>-<run-id>`` and the
    artifacts inside it ``<system-serial>-<procedure_slug>-run.json`` /
    ``-report.html`` (see run_artifact_stem) - serial first, so listings sort
    by unit. The JSON, live under its initial name since the first recorded
    event, is renamed atomically; any rename failure keeps the old name,
    because naming must never cost evidence.
    """
    stem = run_artifact_stem(procedure_slug, result)
    rename_directory = getattr(recorder, "rename_run_directory", None)
    if callable(rename_directory):
        try:
            rename_directory(f"{stem}-{_safe_component(request.run_id)}")
        except OSError as exc:
            emit_detail(output_func,
                        f"run-folder rename failed, keeping "
                        f"{Path(recorder.run_directory).name}: {exc}")
    rename_evidence = getattr(recorder, "rename_evidence", None)
    if callable(rename_evidence):
        try:
            rename_evidence(f"{stem}-run.json")
        except OSError as exc:
            emit_detail(output_func, f"evidence rename failed, keeping "
                                     f"{Path(recorder.json_path).name}: {exc}")
    report_name = f"{stem}-report.html"
    report_path = Path(recorder.run_directory) / report_name
    incomplete_result = replace(
        result,
        report_paths=(Path(recorder.json_path),),
        report_artifact=ReportArtifactEvidence(
            report_path, ReportArtifactStatus.INCOMPLETE
        ),
    )
    recorder.checkpoint(incomplete_result)
    try:
        report = report_factory(recorder.run_directory, report_name)
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
        output_func("Final result: FAIL")
        return 1
    recorder.checkpoint(finalized_result)
    emit_detail(output_func, f"procedure status: {finalized_result.status.value}")
    if finalized_result.status is ProcedureStatus.PASSED:
        verdict = "PASS"
    elif finalized_result.status is ProcedureStatus.CANCELED:
        verdict = "CANCELED"
    else:
        verdict = "FAIL"
    output_func(f"Final result: {verdict}")
    if finalized_result.failure_kind is not None:
        output_func(f"Problem type: {finalized_result.failure_kind.value}")
    if finalized_result.failure_reason is not None:
        output_func(f"Problem: {finalized_result.failure_reason}")
    output_func(f"Saved data (JSON): {Path(recorder.json_path).resolve()}")
    output_func(f"Saved report (HTML): {Path(report_path).resolve()}")
    return 0 if finalized_result.status is ProcedureStatus.PASSED else 1
