"""Run a procedure module with its interactive prompts framed for a host.

``python -m omotion.scripts.framed_prompts <module> [args...]`` executes
``<module>.main(args, input_func=...)`` — the injection seam every WI-00015
script already exposes — with an ``input_func`` that announces each prompt as
one complete, machine-readable stdout line before blocking on stdin:

    @OW-PROMPT@ {"prompt": "Which sensor is installed? (left/right): "}

The line is newline-terminated and flushed, so a host reading the pipe never
has to guess whether an unterminated tail is a prompt or partial output. The
JSON payload carries the exact text the procedure passed to ``input_func``
(JSON-escaped, so a frame is always a single line). The host answers by
writing one newline-terminated line to stdin, exactly as an operator would;
EOF on stdin reaches the procedure as ``EOFError``, its normal cancel path.

The contract a host may rely on:

* Every interactive read in the procedure produces exactly one frame —
  nothing blocks on stdin without one. (The wi15 scripts route all input
  through ``input_func``; a conformance test pins this.)
* Nothing else the wrapper writes to stdout carries the sentinel; ordinary
  procedure output streams unchanged, interleaved with the frames.
* The exit code is the procedure's own, except 2 for "cannot run this
  module at all" (missing, or no conforming ``main``).

This entry point exists for programs hosting a procedure behind a UI (the
test-app Procedures pane). Operators running a procedure by hand should keep
using the plain ``python -m omotion.scripts.wi15_*`` entry points, whose
prompts appear as normal terminal prompts.
"""

from __future__ import annotations

import importlib
import inspect
import json
import sys

# Start-of-line marker for a prompt frame. The trailing space separates it
# from the JSON payload: PROMPT_SENTINEL + json.dumps({"prompt": <text>}).
PROMPT_SENTINEL = "@OW-PROMPT@ "


def framed_input(prompt: str) -> str:
    """``input()``, with the prompt announced as a frame instead of a tail."""
    print(PROMPT_SENTINEL + json.dumps({"prompt": str(prompt)}), flush=True)
    return input()


def _accepts_input_func(main_func) -> bool:
    try:
        parameters = inspect.signature(main_func).parameters
    except (TypeError, ValueError):
        return True  # not introspectable — let the call itself decide
    return "input_func" in parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(
            "usage: python -m omotion.scripts.framed_prompts "
            "<procedure-module> [args...]",
            file=sys.stderr,
        )
        return 2
    module_name, args = argv[0], argv[1:]
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        print(f"procedure {module_name} is not importable: {exc}",
              file=sys.stderr)
        return 2
    main_func = getattr(module, "main", None)
    if not callable(main_func) or not _accepts_input_func(main_func):
        print(
            f"{module_name} does not expose main(argv, *, input_func=...) — "
            f"it cannot be prompt-framed",
            file=sys.stderr,
        )
        return 2
    result = main_func(args, input_func=framed_input)
    return 0 if result is None else int(result)


if __name__ == "__main__":
    raise SystemExit(main())
