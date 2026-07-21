"""Boot-mode detection from `dfu-util -l` output.

Both the ST ROM loader and openmotion-bl enumerate as 0483:df11, so VID/PID
cannot tell them apart. The DFU alt-setting layout can:

  * ROM loader  -> four alts (Internal Flash, Option Bytes, OTP, Device Feature)
                   and a fully-writable flash descriptor ("16*128Kg")
  * openmotion-bl -> one alt, with read-only runs ("01*128Ka,04*128Kg,11*128Ka")
                     because everything outside the app slot is locked
"""

import pytest

from omotion.boot_mode import BootMode, parse_boot_mode


ROM_LISTING = """dfu-util 0.11

Copyright 2005-2009 Weston Schmidt, Harald Welte and OpenMoko Inc.
Copyright 2010-2021 Tormod Volden and Stefan Schmidt

Found DFU: [0483:df11] ver=2200, devnum=27, cfg=1, intf=0, path="1-4", alt=3, name="@Device Feature/0xFFFF0000/01*004 e", serial="200364500000"
Found DFU: [0483:df11] ver=2200, devnum=27, cfg=1, intf=0, path="1-4", alt=2, name="@OTP Memory /0x08FFF000/01*1024 e", serial="200364500000"
Found DFU: [0483:df11] ver=2200, devnum=27, cfg=1, intf=0, path="1-4", alt=1, name="@Option Bytes /0x5200201C/01*128 e", serial="200364500000"
Found DFU: [0483:df11] ver=2200, devnum=27, cfg=1, intf=0, path="1-4", alt=0, name="@Internal Flash /0x08000000/16*128Kg", serial="200364500000"
"""

BL_LISTING = """dfu-util 0.11

Copyright 2005-2009 Weston Schmidt, Harald Welte and OpenMoko Inc.

Found DFU: [0483:df11] ver=0200, devnum=31, cfg=1, intf=0, path="1-4", alt=0, name="@Internal Flash/0x08000000/01*128Ka,04*128Kg,11*128Ka", serial="OWSENSORBL"
"""

NO_DEVICE = """dfu-util 0.11

Copyright 2005-2009 Weston Schmidt, Harald Welte and OpenMoko Inc.

No DFU capable USB device available
"""


def test_rom_loader_listing_is_bare_metal():
    assert parse_boot_mode(ROM_LISTING) is BootMode.BARE_METAL


def test_openmotion_bl_listing_is_bootloader():
    assert parse_boot_mode(BL_LISTING) is BootMode.BOOTLOADER


def test_no_dfu_device_is_unknown():
    assert parse_boot_mode(NO_DEVICE) is BootMode.UNKNOWN


def test_empty_output_is_unknown():
    assert parse_boot_mode("") is BootMode.UNKNOWN


def test_option_bytes_alt_wins_even_if_flash_alt_looks_locked():
    """A ROM listing must never be read as the custom bootloader. The
    ROM-only alt settings are the decisive signal, not the sector layout."""
    weird = ROM_LISTING.replace("16*128Kg", "01*128Ka,15*128Kg")
    assert parse_boot_mode(weird) is BootMode.BARE_METAL


def test_single_fully_writable_alt_is_unknown_not_bootloader():
    """One alt with no read-only run is not openmotion-bl. Refuse to guess
    rather than mis-detect and write to the wrong address."""
    listing = BL_LISTING.replace("01*128Ka,04*128Kg,11*128Ka", "16*128Kg")
    assert parse_boot_mode(listing) is BootMode.UNKNOWN


def test_garbage_output_is_unknown():
    assert parse_boot_mode("wharrgarbl\nnot a dfu listing at all\n") is BootMode.UNKNOWN


@pytest.mark.parametrize("mode,expected", [
    (BootMode.BARE_METAL, "0x08000000"),
    (BootMode.BOOTLOADER, "0x08020000"),
])
def test_flash_address_for_mode(mode, expected):
    """The whole point of detection: which address a normal update writes to."""
    from omotion.boot_mode import flash_address_for

    assert flash_address_for(mode) == expected


def test_flash_address_for_unknown_mode_raises():
    from omotion.boot_mode import flash_address_for

    with pytest.raises(ValueError):
        flash_address_for(BootMode.UNKNOWN)
