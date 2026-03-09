#!/usr/bin/env python3
"""Simple test harness for omotion.jedecParser.parse_jedec_file

Usage: python scripts/test_jed_parser.py [path_to.jed]

If no path is provided, the script will try the default Safety_impl1.jed
location provided by the user request.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Tuple

from omotion.jedecParser import parse_jedec_file, JedecError


DEFAULT_JED = r"C:\Users\gvigelet\CURRENT_WORK\openwater\openmotion-safety-fpga\impl1\Safety_impl1.jed"


def count_bits(data: bytes) -> int:
    return sum(bin(b).count("1") for b in data)


def run(path: Path) -> int:
    if not path.exists():
        print(f"ERROR: file not found: {path}")
        return 2

    try:
        img, extra = parse_jedec_file(str(path))
    except JedecError as e:
        print(f"PARSE ERROR: {e}")
        return 3
    except Exception as e:
        print(f"UNEXPECTED ERROR: {e}")
        return 4

    print("JEDEC parse OK")
    print(f"  Path:        {path}")
    print(f"  Total fuses: {img.total_fuses}")
    print(f"  Rows:        {img.rows}")
    print(f"  Row bytes:   {img.row_size_bytes}")
    print(f"  Total bytes: {len(img.data)}")
    set_bits = count_bits(img.data)
    print(f"  Set bits:    {set_bits} (in packed data)")

    if extra:
        print("  Extra fields:")
        for k, v in extra.items():
            # truncate long bitstrings
            display = v if len(v) <= 80 else (v[:72] + "...")
            print(f"    - {k}: {display}")

    # show first two rows in hex for quick inspection
    rows_to_show = min(2, img.rows)
    for r in range(rows_to_show):
        start = r * img.row_size_bytes
        chunk = img.data[start:start + img.row_size_bytes]
        print(f"  Row {r:02d}: " + " ".join(f"{b:02X}" for b in chunk))

    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2:
        path = Path(argv[1])
    else:
        path = Path(DEFAULT_JED)

    return run(path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
