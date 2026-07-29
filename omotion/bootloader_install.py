"""Convert a bare-metal device to run the openmotion secure bootloader.

**This is irreversible over USB.** Once installed, openmotion-bl clamps its DFU
write window to the application slot and rejects erase of sector 0, so returning
a device to bare metal requires SWD/ST-LINK or a BOOT0 strap into the ROM loader.
The device also accepts only *signed* images from then on, and the bootloader's
monotonic anti-rollback floor means it will permanently refuse any firmware older
than the newest it has booted.

Deliberately segregated from :mod:`omotion.firmware_update`:

* this module is **not** re-exported from ``omotion``, so ``from omotion import
  ...`` cannot reach it — importing it has to be a conscious act;
* :func:`install_bootloader` requires ``acknowledge_irreversible=True``;
* :meth:`omotion.firmware_update.FirmwareUpdater.update` has no code path that
  selects a production image, so ordinary updates can never convert a device.

Applications that must never convert a device (the bloodflow app) simply never
import this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from omotion.boot_mode import BARE_METAL_FLASH_ADDRESS, BootMode
from omotion.DFUProgrammer import DFUProgrammer, DFUProgress, DFUResult
from omotion.firmware_update import FirmwareKind, is_production_asset

__all__ = ["BootloaderInstallError", "install_bootloader"]

_STM32_DFU_VIDPID = "0483:df11"


class BootloaderInstallError(RuntimeError):
    """Raised when a bootloader install cannot or must not proceed."""


def install_bootloader(
    handle,
    kind: FirmwareKind,
    production_bin: Path,
    *,
    acknowledge_irreversible: bool,
    programmer: DFUProgrammer | None = None,
    dfu_wait_timeout_s: float = 30.0,
    progress_cb: Callable[[DFUProgress], None] | None = None,
) -> DFUResult:
    """Flash a production image (bootloader + signed app) at ``0x08000000``.

    ``acknowledge_irreversible`` is keyword-only and mandatory: omitting it is a
    ``TypeError``, so this cannot be called by accident or by code that merely
    forwards ``**kwargs``.

    Aborts **without writing anything** if the device already has a bootloader,
    or if its mode cannot be determined. Boot mode is only observable once the
    device is in DFU, so this is where the "not already installed" guarantee is
    actually enforced — a UI can grey the button out as a courtesy, but the
    refusal has to happen here.
    """
    if acknowledge_irreversible is not True:
        raise BootloaderInstallError(
            "installing a bootloader is irreversible over USB (recovery needs "
            "SWD or a BOOT0 strap); pass acknowledge_irreversible=True to proceed"
        )

    production_bin = Path(production_bin)
    if not is_production_asset(production_bin.name):
        raise BootloaderInstallError(
            f"{production_bin.name} is not a production image. Installing a "
            "bootloader needs the bootloader + signed app image "
            f"({kind.value} builds publish it as motion-{kind.value}-production.bin)."
        )
    if not production_bin.is_file():
        raise BootloaderInstallError(f"production image not found: {production_bin}")

    dfu = programmer or DFUProgrammer(vidpid=_STM32_DFU_VIDPID)

    if not handle.enter_dfu():
        raise BootloaderInstallError("device did not accept enter_dfu()")
    if not dfu.wait_for_dfu_device(timeout_s=dfu_wait_timeout_s):
        raise BootloaderInstallError("DFU device did not appear after enter_dfu()")

    mode = dfu.detect_boot_mode()
    if mode is BootMode.BOOTLOADER:
        raise BootloaderInstallError(
            "this device already has the bootloader installed; nothing was written"
        )
    if mode is not BootMode.BARE_METAL:
        raise BootloaderInstallError(
            "could not confirm this device is bare metal; refusing to write a "
            "production image, because doing so on the wrong device is not undoable"
        )

    return dfu.flash_bin(
        production_bin, address=BARE_METAL_FLASH_ADDRESS, progress=progress_cb
    )
