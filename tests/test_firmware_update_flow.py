"""FirmwareUpdater picks the asset and address from the device's boot mode.

The device is only classifiable once it is already in DFU, so the flow is:
enter DFU -> detect -> choose asset + address -> flash.
"""

from pathlib import Path

import pytest

from omotion.boot_mode import BootMode
from omotion.DFUProgrammer import DFUResult
from omotion.firmware_update import (
    FirmwareKind,
    FirmwareUpdateError,
    FirmwareUpdater,
    UnsupportedReleaseError,
    register_download,
)


class FakeHandle:
    def __init__(self, accepts_dfu=True):
        self.accepts_dfu = accepts_dfu
        self.enter_dfu_calls = 0

    def enter_dfu(self):
        self.enter_dfu_calls += 1
        return self.accepts_dfu


class FakeProgrammer:
    """Stands in for DFUProgrammer; records what would have been flashed."""

    def __init__(self, mode=BootMode.BARE_METAL, appears=True):
        self._mode = mode
        self._appears = appears
        self.flashed = []          # list of (path, address)

    def wait_for_dfu_device(self, *, timeout_s=30.0):
        return self._appears

    def detect_boot_mode(self):
        return self._mode

    def flash_bin(self, bin_path, *, address, progress=None):
        self.flashed.append((Path(bin_path), address))
        return DFUResult(command=[], returncode=0, stdout="", success=True)


@pytest.fixture
def release_dir(tmp_path):
    """A download directory holding every candidate for a new-style release."""
    for name in (
        "motion-sensor-fw-baremetal-fpga.bin",
        "motion-sensor-fw-signed.bin",
    ):
        (tmp_path / name).write_bytes(b"\x00" * 16)
    return tmp_path


def test_bare_metal_device_flashes_baremetal_image_at_flash_base(release_dir):
    prog = FakeProgrammer(mode=BootMode.BARE_METAL)
    updater = FirmwareUpdater(programmer=prog)
    primary = release_dir / "motion-sensor-fw-baremetal-fpga.bin"
    register_download(primary, FirmwareKind.SENSOR, "1.8.2")

    updater.update(FakeHandle(), primary)

    assert prog.flashed == [(primary, "0x08000000")]


def test_bootloader_device_flashes_signed_image_into_the_slot(release_dir):
    """Same starting path as the bare-metal case — the detected mode, not the
    caller, decides which sibling is used."""
    prog = FakeProgrammer(mode=BootMode.BOOTLOADER)
    updater = FirmwareUpdater(programmer=prog)
    primary = release_dir / "motion-sensor-fw-baremetal-fpga.bin"
    register_download(primary, FirmwareKind.SENSOR, "1.8.2")

    updater.update(FakeHandle(), primary)

    assert prog.flashed == [(release_dir / "motion-sensor-fw-signed.bin", "0x08020000")]


def test_unknown_mode_refuses_to_flash_anything(release_dir):
    prog = FakeProgrammer(mode=BootMode.UNKNOWN)
    updater = FirmwareUpdater(programmer=prog)
    primary = release_dir / "motion-sensor-fw-baremetal-fpga.bin"
    register_download(primary, FirmwareKind.SENSOR, "1.8.2")

    with pytest.raises(FirmwareUpdateError):
        updater.update(FakeHandle(), primary)
    assert prog.flashed == []


def test_bootloader_device_with_legacy_release_refuses(tmp_path):
    legacy = tmp_path / "motion-sensor-fw.bin"
    legacy.write_bytes(b"\x00" * 16)
    register_download(legacy, FirmwareKind.SENSOR, "1.8.1")
    prog = FakeProgrammer(mode=BootMode.BOOTLOADER)

    with pytest.raises(UnsupportedReleaseError) as exc:
        FirmwareUpdater(programmer=prog).update(FakeHandle(), legacy)
    assert "1.8.2" in str(exc.value)
    assert prog.flashed == []


def test_legacy_release_still_flashes_on_a_bare_metal_device(tmp_path):
    legacy = tmp_path / "motion-sensor-fw.bin"
    legacy.write_bytes(b"\x00" * 16)
    register_download(legacy, FirmwareKind.SENSOR, "1.8.1")
    prog = FakeProgrammer(mode=BootMode.BARE_METAL)

    FirmwareUpdater(programmer=prog).update(FakeHandle(), legacy)

    assert prog.flashed == [(legacy, "0x08000000")]


def test_device_refusing_dfu_raises_before_flashing():
    prog = FakeProgrammer()
    with pytest.raises(FirmwareUpdateError):
        FirmwareUpdater(programmer=prog).update(FakeHandle(accepts_dfu=False), Path("x.bin"))
    assert prog.flashed == []


def test_dfu_device_not_appearing_raises(release_dir):
    prog = FakeProgrammer(appears=False)
    primary = release_dir / "motion-sensor-fw-baremetal-fpga.bin"
    with pytest.raises(FirmwareUpdateError):
        FirmwareUpdater(programmer=prog).update(FakeHandle(), primary)
    assert prog.flashed == []


def test_unregistered_signed_image_refused_on_bare_metal_device(tmp_path):
    """The 'Upload File...' path: an arbitrary file the SDK never downloaded.
    A signed slot image at 0x08000000 would land on the bootloader sector."""
    stray = tmp_path / "motion-sensor-fw-signed.bin"
    stray.write_bytes(b"\x00" * 16)
    prog = FakeProgrammer(mode=BootMode.BARE_METAL)

    with pytest.raises(FirmwareUpdateError):
        FirmwareUpdater(programmer=prog).update(FakeHandle(), stray)
    assert prog.flashed == []


def test_unregistered_baremetal_image_refused_on_bootloader_device(tmp_path):
    stray = tmp_path / "motion-sensor-fw-baremetal-fpga.bin"
    stray.write_bytes(b"\x00" * 16)
    prog = FakeProgrammer(mode=BootMode.BOOTLOADER)

    with pytest.raises(FirmwareUpdateError):
        FirmwareUpdater(programmer=prog).update(FakeHandle(), stray)
    assert prog.flashed == []


def test_unregistered_production_image_refused_by_update(tmp_path):
    """A production image converts a device to bootloader mode. That is never an
    update, and update() must not be a back door to it."""
    stray = tmp_path / "motion-sensor-production.bin"
    stray.write_bytes(b"\x00" * 16)
    prog = FakeProgrammer(mode=BootMode.BARE_METAL)

    with pytest.raises(FirmwareUpdateError) as exc:
        FirmwareUpdater(programmer=prog).update(FakeHandle(), stray)
    assert "install_bootloader" in str(exc.value)
    assert prog.flashed == []


def test_unregistered_unrecognisable_file_flashes_at_the_mode_address(tmp_path):
    """A file we cannot classify by name is taken at face value and written to
    whatever the detected mode calls for."""
    stray = tmp_path / "custom-build.bin"
    stray.write_bytes(b"\x00" * 16)
    prog = FakeProgrammer(mode=BootMode.BOOTLOADER)

    FirmwareUpdater(programmer=prog).update(FakeHandle(), stray)

    assert prog.flashed == [(stray, "0x08020000")]
