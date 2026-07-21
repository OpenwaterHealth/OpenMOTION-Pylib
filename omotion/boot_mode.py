"""Which bootloader is a device running?

An openmotion board in DFU is either sitting in the STM32 ROM loader (no custom
bootloader installed — a "bare metal" unit) or in openmotion-bl / open-motion-
console-bl (a converted unit). Both enumerate as **0483:df11**, so VID/PID tells
you nothing. The DFU alt-setting layout does:

  ROM loader      four alts — Internal Flash, Option Bytes, OTP Memory, Device
                  Feature — and a fully-writable flash descriptor (``16*128Kg``).
  openmotion-bl   one alt, whose descriptor carries read-only runs
                  (``01*128Ka,04*128Kg,11*128Ka``) because everything outside the
                  application slot is deliberately locked.

That difference decides where a firmware update is written: ``0x08000000`` for a
bare-metal image, ``0x08020000`` for a signed image in the bootloader's slot.
Writing one to the other's address produces a brick, so when the evidence is
ambiguous this module reports :data:`BootMode.UNKNOWN` and callers must refuse to
flash rather than guess.
"""

from __future__ import annotations

import re
from enum import Enum

__all__ = ["BootMode", "parse_boot_mode", "flash_address_for"]


class BootMode(Enum):
    BARE_METAL = "bare_metal"
    """STM32 ROM loader: no custom bootloader installed."""

    BOOTLOADER = "bootloader"
    """openmotion-bl / open-motion-console-bl."""

    UNKNOWN = "unknown"
    """Could not tell. Never flash on this."""


#: Bare-metal images link and flash at the base of flash.
BARE_METAL_FLASH_ADDRESS = "0x08000000"

#: Signed images live in the bootloader's active slot (vectors at +0x400).
BOOTLOADER_SLOT_ADDRESS = "0x08020000"

_FLASH_ADDRESS = {
    BootMode.BARE_METAL: BARE_METAL_FLASH_ADDRESS,
    BootMode.BOOTLOADER: BOOTLOADER_SLOT_ADDRESS,
}

# `Found DFU: [0483:df11] ver=..., ..., alt=0, name="@Internal Flash/...", serial="..."`
_ALT_RE = re.compile(r"\balt=(\d+)\b")
_NAME_RE = re.compile(r'\bname="([^"]*)"')

# DfuSe sector spec: <count>*<size><multiplier><access>, access = 'a'+bitmask
# where bit0=readable, bit1=erasable, bit2=writable. So 'a' is read-only and
# 'g' is read/erase/write.
_SECTOR_RE = re.compile(r"\d+\s*\*\s*\d+\s*[KMB]?\s*([a-g])")

# Alt settings only the ST ROM loader exposes. Presence of any of these is
# decisive: a listing containing them is the ROM loader regardless of what the
# flash descriptor looks like.
_ROM_ONLY_ALTS = ("option bytes", "otp memory", "device feature")


def _alt_entries(dfu_util_output: str) -> list[tuple[int, str]]:
    """Extract ``(alt, name)`` for each `Found DFU:` line."""
    entries: list[tuple[int, str]] = []
    for line in dfu_util_output.splitlines():
        if "found dfu" not in line.lower():
            continue
        alt_m = _ALT_RE.search(line)
        name_m = _NAME_RE.search(line)
        if alt_m is None or name_m is None:
            continue
        entries.append((int(alt_m.group(1)), name_m.group(1)))
    return entries


def parse_boot_mode(dfu_util_output: str) -> BootMode:
    """Classify a ``dfu-util -l`` listing. Never raises."""
    entries = _alt_entries(dfu_util_output or "")
    if not entries:
        return BootMode.UNKNOWN

    if any(marker in name.lower() for _, name in entries for marker in _ROM_ONLY_ALTS):
        return BootMode.BARE_METAL

    if len(entries) == 1:
        _, name = entries[0]
        if any(access == "a" for access in _SECTOR_RE.findall(name)):
            return BootMode.BOOTLOADER

    return BootMode.UNKNOWN


def flash_address_for(mode: BootMode) -> str:
    """Address a normal firmware update writes to under ``mode``.

    Raises ``ValueError`` for :data:`BootMode.UNKNOWN` — there is no safe
    default, and picking one is how a device gets bricked.
    """
    try:
        return _FLASH_ADDRESS[mode]
    except KeyError:
        raise ValueError(
            f"cannot choose a flash address for boot mode {mode.value!r}; "
            "refusing to guess"
        ) from None
