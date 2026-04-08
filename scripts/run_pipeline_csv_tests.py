"""
Standalone science pipeline test runner.

Runs all CSV-fixture-based pipeline tests without requiring pytest.
No hardware is needed — tests are driven entirely by pre-generated
histogram CSV files in tests/fixtures/.

Usage
-----
From the repo root:

    python scripts/run_pipeline_csv_tests.py

If fixtures are missing, the script offers to generate them automatically.

Exit codes
----------
0  all tests passed
1  one or more tests failed
2  fixture files are missing and generation was declined / failed
"""

import os
import subprocess
import sys

# Locate project root relative to this script.
_SCRIPTS_DIR  = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPTS_DIR)
_FIXTURES_DIR = os.path.join(_PROJECT_ROOT, "tests", "fixtures")
_GENERATOR    = os.path.join(_FIXTURES_DIR, "generate_fixtures.py")
_TEST_MODULE  = os.path.join(_PROJECT_ROOT, "tests", "test_pipeline_csv.py")

_REQUIRED_FIXTURES = [
    "single_cam_basic_left.csv",
    "multi_cam_left.csv",
    "both_sides_left.csv",
    "both_sides_right.csv",
    "frame_id_rollover_left.csv",
    "multi_interval_left.csv",
]

# Add project root to path so omotion can be imported without installing.
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Fixture check / generation
# ---------------------------------------------------------------------------

def _missing_fixtures() -> list[str]:
    return [
        f for f in _REQUIRED_FIXTURES
        if not os.path.isfile(os.path.join(_FIXTURES_DIR, f))
    ]


def _generate_fixtures() -> bool:
    """Run the fixture generator and return True on success."""
    print("Generating fixture CSV files …")
    result = subprocess.run(
        [sys.executable, _GENERATOR],
        cwd=_PROJECT_ROOT,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 60)
    print("OpenMOTION science pipeline — CSV fixture tests")
    print("=" * 60)

    # --- Fixture availability check -----------------------------------------
    missing = _missing_fixtures()
    if missing:
        print(f"\nMissing {len(missing)} fixture(s):")
        for f in missing:
            print(f"  {f}")
        print(f"\nRun the generator to create them:")
        print(f"  python {os.path.relpath(_GENERATOR, _PROJECT_ROOT)}")
        print()

        # Auto-generate if running non-interactively (e.g. CI) or if the user
        # confirms.
        if sys.stdin.isatty():
            answer = input("Generate now? [Y/n] ").strip().lower()
            if answer in ("", "y", "yes"):
                if not _generate_fixtures():
                    print("ERROR: fixture generation failed.")
                    return 2
            else:
                return 2
        else:
            # Non-interactive — generate automatically.
            if not _generate_fixtures():
                print("ERROR: fixture generation failed.")
                return 2

    # --- Run tests -----------------------------------------------------------
    print()
    # Import here so path manipulation above takes effect first.
    from tests.test_pipeline_csv import run_standalone  # noqa: E402
    return run_standalone()


if __name__ == "__main__":
    sys.exit(main())
