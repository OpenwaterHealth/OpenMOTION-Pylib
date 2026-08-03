"""Qisda serial number validation and generation for Openwater Motion / LIFU products.

Format (11 characters), per "Qisda Serial Number naming rule":

    W  WWA  4  Q  4  0  001
    |   |   |  |  |  |   |
    |   |   |  |  |  |   +-- Rolling number (001-999)
    |   |   |  |  |  +------ Qisda MFG (fixed '0')
    |   |   |  |  +--------- MFG month code
    |   |   |  +------------ MFG year code (Q=2026, R=2027, S=2028, T=2029)
    |   |   +--------------- Product stage (DVT=4, PVT=5, MP=6)
    |   +------------------- Product category (3 chars)
    +----------------------- Openwater (fixed 'W')

The rolling number is tracked per unique serial prefix (everything before the
rolling number) in a small JSON file, so counts reset naturally for each
category/stage/year/month combination.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# --- Field code tables -------------------------------------------------------

OPENWATER_PREFIX = "W"
QISDA_MFG = "0"  # fixed

MOTION_CATEGORIES = {
    "900-00013": "WW0",
    "900-00010": "WWA",
}

LIFU_CATEGORIES = {
    "TOP_LEVEL": "L11",
    "CONSOLE": "L01",
    "ENCLOSURE_1X_155": "L02",
    "ENCLOSURE_2X_155": "L04",
    "ENCLOSURE_1X_400": "L06",
    "ENCLOSURE_2X_400": "L08",
    "TRANSMIT_MODULE_155": "LM1",
    "TRANSMIT_MODULE_400": "LM0",
}

# code -> (family, product name)
CATEGORY_CODES = {code: ("motion", name) for name, code in MOTION_CATEGORIES.items()}
CATEGORY_CODES.update({code: ("lifu", name) for name, code in LIFU_CATEGORIES.items()})

STAGES = {"DVT": "4", "PVT": "5", "MP": "6"}
STAGE_CODES = {v: k for k, v in STAGES.items()}

YEARS = {2026: "Q", 2027: "R", 2028: "S", 2029: "T"}
YEAR_CODES = {v: k for k, v in YEARS.items()}

# Rule sheet documents 4=Apr .. 7=Jul; months 1-9 follow the same digit
# pattern. Oct-Dec codes (A/B/C) are an assumption pending Qisda confirmation.
MONTHS = {m: str(m) for m in range(1, 10)}
MONTHS.update({10: "A", 11: "B", 12: "C"})
MONTH_CODES = {v: k for k, v in MONTHS.items()}

_SERIAL_RE = re.compile(
    r"^W"
    r"(?P<category>[A-Z0-9]{3})"
    r"(?P<stage>[456])"
    r"(?P<year>[QRST])"
    r"(?P<month>[1-9ABC])"
    r"0"
    r"(?P<rolling>\d{3})$"
)


class SerialNumberError(ValueError):
    """Raised when a serial number is malformed or a field is unknown."""


@dataclass(frozen=True)
class SerialNumber:
    """A parsed, valid Qisda serial number."""

    serial: str
    family: str          # "motion" or "lifu"
    product: str         # e.g. "900-00013" or "CONSOLE"
    category_code: str   # e.g. "WW0", "L01"
    stage: str           # "DVT", "PVT", "MP"
    year: int            # e.g. 2026
    month: int           # 1-12
    rolling: int         # 1-999

    def __str__(self) -> str:
        return self.serial


class QisdaSerialManager:
    """Validates existing serials and generates new ones with tracked rolling numbers."""

    def __init__(self, counter_file: str | Path = "serial_counters.json"):
        self.counter_file = Path(counter_file)

    # --- Validation ----------------------------------------------------------

    @staticmethod
    def parse(serial: str) -> SerialNumber:
        """Parse and validate a serial number; raises SerialNumberError if invalid."""
        serial = serial.strip().upper()
        m = _SERIAL_RE.match(serial)
        if not m:
            raise SerialNumberError(
                f"{serial!r} does not match the 11-char format "
                f"W<CAT:3><STAGE><YEAR><MONTH>0<ROLL:3>"
            )

        cat = m.group("category")
        if cat not in CATEGORY_CODES:
            raise SerialNumberError(f"Unknown product category code {cat!r}")
        family, product = CATEGORY_CODES[cat]

        rolling = int(m.group("rolling"))
        if rolling == 0:
            raise SerialNumberError("Rolling number must be 001-999")

        return SerialNumber(
            serial=serial,
            family=family,
            product=product,
            category_code=cat,
            stage=STAGE_CODES[m.group("stage")],
            year=YEAR_CODES[m.group("year")],
            month=MONTH_CODES[m.group("month")],
            rolling=rolling,
        )

    @classmethod
    def is_valid(cls, serial: str) -> bool:
        try:
            cls.parse(serial)
            return True
        except SerialNumberError:
            return False

    # --- Generation ----------------------------------------------------------

    def create(self, product: str, stage: str, year: int, month: int) -> SerialNumber:
        """Create the next serial number for the given product/stage/year/month.

        product: a Motion part number ("900-00013", "900-00010"), a LIFU product
                 name ("TOP_LEVEL", "CONSOLE", "ENCLOSURE_1X_155", ...), or a raw
                 category code ("WW0", "L11", "LM0", ...).
        stage:   "DVT", "PVT", or "MP"
        year:    2026-2029
        month:   1-12
        """
        cat = self._resolve_category(product)

        stage_key = stage.strip().upper()
        if stage_key not in STAGES:
            raise SerialNumberError(f"Unknown stage {stage!r}; expected one of {list(STAGES)}")
        if year not in YEARS:
            raise SerialNumberError(f"Unsupported year {year}; expected one of {list(YEARS)}")
        if month not in MONTHS:
            raise SerialNumberError(f"Invalid month {month}; expected 1-12")

        prefix = f"{OPENWATER_PREFIX}{cat}{STAGES[stage_key]}{YEARS[year]}{MONTHS[month]}{QISDA_MFG}"
        rolling = self._next_rolling(prefix)
        return self.parse(f"{prefix}{rolling:03d}")

    @staticmethod
    def _resolve_category(product: str) -> str:
        key = product.strip().upper()
        if key in MOTION_CATEGORIES:
            return MOTION_CATEGORIES[key]
        if key in LIFU_CATEGORIES:
            return LIFU_CATEGORIES[key]
        if key in CATEGORY_CODES:
            return key  # already a raw category code
        known = list(MOTION_CATEGORIES) + list(LIFU_CATEGORIES) + list(CATEGORY_CODES)
        raise SerialNumberError(f"Unknown product {product!r}; expected one of {known}")

    # --- Rolling number persistence -------------------------------------------

    def _load_counters(self) -> dict[str, int]:
        if self.counter_file.exists():
            return json.loads(self.counter_file.read_text())
        return {}

    def _next_rolling(self, prefix: str) -> int:
        counters = self._load_counters()
        next_num = counters.get(prefix, 0) + 1
        if next_num > 999:
            raise SerialNumberError(f"Rolling number exhausted (999) for prefix {prefix}")
        counters[prefix] = next_num
        self.counter_file.write_text(json.dumps(counters, indent=2, sort_keys=True))
        return next_num

    def register(self, serial: str) -> SerialNumber:
        """Record an externally issued serial so future create() calls continue after it."""
        parsed = self.parse(serial)
        prefix = parsed.serial[:8]
        counters = self._load_counters()
        if parsed.rolling > counters.get(prefix, 0):
            counters[prefix] = parsed.rolling
            self.counter_file.write_text(json.dumps(counters, indent=2, sort_keys=True))
        return parsed


if __name__ == "__main__":
    mgr = QisdaSerialManager()

    # Generate a few serials
    s1 = mgr.create("900-00013", stage="DVT", year=2026, month=4)
    s2 = mgr.create("900-00013", stage="DVT", year=2026, month=4)
    s3 = mgr.create("CONSOLE", stage="PVT", year=2027, month=6)
    print(f"Motion: {s1}  {s2}")
    print(f"LIFU:   {s3}")

    # Validate
    for sn in [str(s1), "WWWA4Q40001", "WL014RQ0001", "XXX", "WZZZ4Q40001"]:
        if QisdaSerialManager.is_valid(sn):
            p = QisdaSerialManager.parse(sn)
            print(f"{sn}: VALID  ({p.family} {p.product}, {p.stage}, {p.month}/{p.year}, #{p.rolling})")
        else:
            print(f"{sn}: INVALID")
