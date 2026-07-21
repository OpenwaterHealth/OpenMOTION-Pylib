"""Installing a bootloader is a separate, deliberately awkward operation.

Converting a device to bootloader mode is irreversible over USB: openmotion-bl
clamps its DFU write window to the application slot and rejects erase of sector
0, so getting back to bare metal needs SWD or a BOOT0 strap. The bloodflow app
must not be able to do it by accident, and neither must an ordinary update.
"""

from pathlib import Path

import pytest

from omotion.boot_mode import BootMode
from omotion.bootloader_install import (
    BootloaderInstallError,
    install_bootloader,
)
from omotion.DFUProgrammer import DFUProgrammer, DFUResult
from omotion.firmware_update import FirmwareKind


class FakeHandle:
    def __init__(self, accepts_dfu=True):
        self.accepts_dfu = accepts_dfu

    def enter_dfu(self):
        return self.accepts_dfu


class FakeProgrammer:
    def __init__(self, mode=BootMode.BARE_METAL, appears=True):
        self._mode = mode
        self._appears = appears
        self.flashed = []

    def wait_for_dfu_device(self, *, timeout_s=30.0):
        return self._appears

    def detect_boot_mode(self):
        return self._mode

    def flash_bin(self, bin_path, *, address, progress=None):
        self.flashed.append((Path(bin_path), address))
        return DFUResult(command=[], returncode=0, stdout="", success=True)


@pytest.fixture
def production_image(tmp_path):
    p = tmp_path / "motion-sensor-production.bin"
    p.write_bytes(b"\x00" * 32)
    return p


def test_requires_explicit_acknowledgement(production_image):
    prog = FakeProgrammer()
    with pytest.raises(TypeError):
        install_bootloader(FakeHandle(), FirmwareKind.SENSOR, production_image,
                           programmer=prog)          # no acknowledge_irreversible
    assert prog.flashed == []


def test_acknowledgement_must_be_true(production_image):
    prog = FakeProgrammer()
    with pytest.raises(BootloaderInstallError):
        install_bootloader(FakeHandle(), FirmwareKind.SENSOR, production_image,
                           acknowledge_irreversible=False, programmer=prog)
    assert prog.flashed == []


def test_installs_production_image_at_flash_base(production_image):
    prog = FakeProgrammer(mode=BootMode.BARE_METAL)

    install_bootloader(FakeHandle(), FirmwareKind.SENSOR, production_image,
                       acknowledge_irreversible=True, programmer=prog)

    assert prog.flashed == [(production_image, "0x08000000")]


def test_aborts_without_writing_if_bootloader_already_present(production_image):
    """The 'only available if not already installed' guarantee. The UI cannot
    know the mode before entering DFU, so the refusal happens here."""
    prog = FakeProgrammer(mode=BootMode.BOOTLOADER)

    with pytest.raises(BootloaderInstallError) as exc:
        install_bootloader(FakeHandle(), FirmwareKind.SENSOR, production_image,
                           acknowledge_irreversible=True, programmer=prog)
    assert "already" in str(exc.value).lower()
    assert prog.flashed == []


def test_aborts_when_mode_cannot_be_determined(production_image):
    prog = FakeProgrammer(mode=BootMode.UNKNOWN)
    with pytest.raises(BootloaderInstallError):
        install_bootloader(FakeHandle(), FirmwareKind.SENSOR, production_image,
                           acknowledge_irreversible=True, programmer=prog)
    assert prog.flashed == []


def test_refuses_an_image_that_is_not_a_production_image(tmp_path):
    wrong = tmp_path / "motion-sensor-fw-signed.bin"
    wrong.write_bytes(b"\x00" * 32)
    prog = FakeProgrammer()

    with pytest.raises(BootloaderInstallError):
        install_bootloader(FakeHandle(), FirmwareKind.SENSOR, wrong,
                           acknowledge_irreversible=True, programmer=prog)
    assert prog.flashed == []


def test_not_reachable_from_the_omotion_package_surface():
    """The bloodflow app imports from `omotion`. Installation must not be there
    — reaching it has to be a deliberate submodule import."""
    import omotion

    assert not hasattr(omotion, "install_bootloader")
    assert "install_bootloader" not in getattr(omotion, "__all__", [])


# ---------------------------------------------------------------------------
# DFUProgrammer.detect_boot_mode delegates to the parser
# ---------------------------------------------------------------------------

BL_LISTING = (
    'Found DFU: [0483:df11] ver=0200, devnum=31, cfg=1, intf=0, path="1-4", '
    'alt=0, name="@Internal Flash/0x08000000/01*128Ka,04*128Kg,11*128Ka", serial="OWBL"\n'
)


def test_detect_boot_mode_uses_the_dfu_listing():
    class Programmer(DFUProgrammer):
        def list_devices(self):
            return BL_LISTING

    assert Programmer(vidpid="0483:df11").detect_boot_mode() is BootMode.BOOTLOADER


def test_detect_boot_mode_unknown_when_nothing_is_listed():
    class Programmer(DFUProgrammer):
        def list_devices(self):
            return "No DFU capable USB device available\n"

    assert Programmer(vidpid="0483:df11").detect_boot_mode() is BootMode.UNKNOWN
