"""WI-00015 Device Specific Parameter Tuning - CLI.

Thin command-line front end over :mod:`omotion.tuning`, which holds the phase
engine. See scripts/WI15_RUNNER_README.md for the deviation log, the
authoritative WI interpretation rulings, and the run sequence.

    python scripts/wi15_runner.py baseline   --side right
    python scripts/wi15_runner.py baseline   --side left
    python scripts/wi15_runner.py tune       --seated <higher-power side>
    python scripts/wi15_runner.py crosscheck --seated <other side>
    python scripts/wi15_runner.py finalize
    python scripts/wi15_runner.py diagnose-low --seated <side>   # diagnostic sweep
    python scripts/wi15_runner.py reset-state
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from omotion.tuning import (
    STATE_FILE,
    phase_baseline,
    phase_crosscheck,
    phase_diagnose_low,
    phase_finalize,
    phase_tune,
)


def main() -> int:
    logging.disable(logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("baseline")
    p.add_argument("--side", required=True, choices=["left", "right"])
    p.add_argument("--window", nargs=2, type=float, metavar=("MIN_UJ", "MAX_UJ"),
                   help="override the SPEC-31 acceptance window for this run "
                        "(default: production 300 400). Pinned into the run "
                        "state; later phases reuse it automatically.")
    p = sub.add_parser("tune")
    p.add_argument("--seated", required=True, choices=["left", "right"])
    p = sub.add_parser("crosscheck")
    p.add_argument("--seated", required=True, choices=["left", "right"])
    sub.add_parser("finalize")
    p = sub.add_parser("diagnose-low")
    p.add_argument("--seated", required=True, choices=["left", "right"])
    sub.add_parser("reset-state")
    args = ap.parse_args()

    if args.cmd == "reset-state":
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
            print("state cleared")
        return 0
    return {"baseline": phase_baseline, "tune": phase_tune,
            "crosscheck": phase_crosscheck, "finalize": phase_finalize,
            "diagnose-low": phase_diagnose_low}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
