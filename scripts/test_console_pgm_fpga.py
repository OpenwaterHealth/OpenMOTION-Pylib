#!/usr/bin/env python3
"""
stm32_dfu_programmer.py

Put a MOTION STM32 board into DFU mode and flash a .bin file.
The script automatically selects the appropriate dfu‑util binary from the
local DFU-UTIL/ sub‑folders (darwin‑x86_64, linux‑amd64, win32, win64).

Usage
-----
    python stm32_dfu_programmer.py <path_to_bin_file>
"""

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_console_pgm_fpga.py

# --------------------------------------------------------------------------- #
#   Standard library imports
# --------------------------------------------------------------------------- #
import argparse
import sys
from pathlib import Path
from omotion.CommandError import CommandError
from omotion.FPGAProgrammer import FpgaPageProgrammer, FpgaUpdateError
from omotion.Interface import MOTIONInterface
from omotion.config import MuxChannel



# --------------------------------------------------------------------------- #
# Test runner helpers
# --------------------------------------------------------------------------- #

_PASS  = "\033[32mPASS\033[0m"
_FAIL  = "\033[31mFAIL\033[0m"
_SKIP  = "\033[33mSKIP\033[0m"

_results: list[tuple[str, str, str]] = []  # (name, status, detail)


def _record(name: str, passed: bool, detail: str = "") -> None:
    status = _PASS if passed else _FAIL
    _results.append((name, status, detail))
    print(f"  [{status}] {name}" + (f": {detail}" if detail else ""))


def _run_summary() -> int:
    """Print a summary table and return the number of failures."""
    total  = len(_results)
    passed = sum(1 for _, s, _ in _results if "PASS" in s)
    failed = total - passed
    print("\n" + "=" * 60)
    print(f"Results: {passed}/{total} passed", end="")
    if failed:
        print(f"  ({failed} FAILED)", end="")
    print("\n" + "=" * 60)
    return failed

# --------------------------------------------------------------------------- #
#   CLI handling
# --------------------------------------------------------------------------- #

def test_fpga_page_program(
    hw: MOTIONInterface,
    myFpga: MuxChannel,
    jedec_path: str,
    verify: bool = True,
    erase_timeout: float = 15.0,
    refresh_timeout: float = 10.0,
) -> None:
    """Page-by-page FPGA programming via direct XO2ECAcmd_* commands."""

    def _progress(pages_done: int, total_pages: int) -> None:
        pct  = 100.0 * pages_done / total_pages if total_pages else 0.0
        bars = int(pct / 5)
        bar  = "#" * bars + "-" * (20 - bars)
        print(
            f"\r    Program: [{bar}] {pct:5.1f}%  {pages_done:>5}/{total_pages} pages",
            end="",
            flush=True,
        )

    programmer = FpgaPageProgrammer(
        hw,
        verify=verify,
        erase_timeout=erase_timeout,
        refresh_timeout=refresh_timeout,
    )
    try:
        programmer.program_from_jedec( target_fpga=myFpga, jedec_path=jedec_path, on_progress=_progress)
        print()  # newline after progress bar
        _record("FPGA_PAGE_PROGRAM", True)
    except (FpgaUpdateError, CommandError) as exc:
        print()  # newline after partial progress bar
        _record("FPGA_PAGE_PROGRAM", False, str(exc))



def parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Flash a Open Motion Console FPGA."
    )
    parser.add_argument(
        "jedec",
        type=Path,
        help="Path to a JEDEC bitstream file (enables FPGA page-by-page programming)",
    )
    parser.add_argument(
        "--fpga",
        type=int,
        default=0,
        choices=[0, 1],
        help="Target FPGA mux channel index (0 or 1).",
    )
    parser.add_argument(
        "--no-verify", action="store_true", dest="no_verify",
        help="Skip read-back verification when using --paged",
    )
    parser.add_argument(
        "--erase-timeout", type=float, default=35.0, dest="erase_timeout",
        help="Seconds to wait for FPGA flash erase (default 35.0; MachXO2-7000 needs ~30 s)",
    )
    parser.add_argument(
        "--refresh-timeout", type=float, default=10.0, dest="refresh_timeout",
        help="Seconds to wait for FPGA refresh when using --paged (default 10.0)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable DEBUG-level logging",
    )
    
    return parser.parse_args()


def main() -> None:
    args = parse_cli()

    # --------------------------------------------------------------- #
    # 0️⃣  Acquire the MOTION interface – exactly as you already do
    # --------------------------------------------------------------- #
    print("[*] Acquiring MOTION interface …")
    interface, console_connected, left_sensor, right_sensor = (
        MOTIONInterface.acquire_motion_interface()
    )

    if not console_connected:
        print("❌  Console module not connected – cannot continue.")
        sys.exit(1)

    print("✅  Console module is connected.\n")
    console = interface.console_module

    if args.jedec:
        jedec_path = Path(args.jedec)
        if not jedec_path.exists():
            print(f"  [SKIP] FPGA tests: file not found: {jedec_path}")
        else:
            print(f"\n--- FPGA page-by-page program from {jedec_path.name} ---")
            test_fpga_page_program(
                console,
                MuxChannel(args.fpga),
                str(jedec_path),
                verify=not args.no_verify,
                erase_timeout=args.erase_timeout,
                refresh_timeout=args.refresh_timeout,
            )

    return _run_summary()

if __name__ == "__main__":
    main()