import os
import argparse

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python data-processing/parse_data_v2.py

from omotion.MotionProcessing import process_bin_file


def parse_args():
    parser = argparse.ArgumentParser(description="Process a histogram .bin file and output .csv")

    parser.add_argument(
        "--file",
        type=str,
        default="histogram.bin",
        help="Path to input .bin file (default: histogram.bin)"
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional CSV output filename. If not provided, defaults to <input>.csv"
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    input_file = args.file
    output_file = args.output if args.output else os.path.splitext(input_file)[0] + ".csv"

    process_bin_file(input_file, output_file, start_offset=0)
