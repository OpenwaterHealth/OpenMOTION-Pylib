# OpenMotion USB Drivers

Cross-platform driver package for the OpenMotion blood-flow sensor system.

## Hardware

The OpenMotion system uses STM32-based USB devices with the following identifiers:

| Device | VID | PID | Type | Description |
|--------|-----|-----|------|-------------|
| Sensor Module (IF0) | `0x0483` | `0x5A5A` MI_00 | Custom USB | Command/control interface |
| Sensor Module (IF1) | `0x0483` | `0x5A5A` MI_01 | Custom USB | Histogram data stream |
| Sensor Module (IF2) | `0x0483` | `0x5A5A` MI_02 | Custom USB | IMU data stream |
| Console Module | `0x0483` | `0xA53E` | CDC/VCP | Serial console (trigger, TEC, telemetry) |
| DFU Bootloader | `0x0483` | `0xDF11` | DFU | Firmware update mode |

## Platform Setup

### Windows

The sensor module requires WinUSB drivers to be installed for each of its three USB interfaces. The console module uses the built-in Windows CDC driver and requires no additional setup.

```
cd drivers\windows
install.bat          (Run as Administrator)
```

This installs the signing certificate and registers the WinUSB `.inf` files via `pnputil`. After installation, unplug and replug the sensor module.

### Linux

Linux requires udev rules to grant non-root users access to the USB devices.

```bash
cd drivers/linux
sudo ./install.sh
```

This copies the udev rules to `/etc/udev/rules.d/`, creates the `plugdev` group if needed, adds your user to it, and reloads udev. You may need to log out and back in for group membership to take effect.

### macOS

No driver installation is required. macOS provides native USB access through IOKit. The only prerequisite is libusb for the sensor modules:

```bash
brew install libusb
```

The console module enumerates as `/dev/cu.usbmodemXXXX` automatically.

## Directory Structure

```
drivers/
  README.md                         This file
  linux/
    99-openmotion.rules             udev rules for non-root USB access
    install.sh                      Installer script (run with sudo)
  windows/
    openmotion-sensor-if0.inf       WinUSB driver for sensor interface 0
    openmotion-sensor-if1.inf       WinUSB driver for sensor interface 1
    openmotion-sensor-if2.inf       WinUSB driver for sensor interface 2
    install.bat                     Installer script (run as Administrator)
  macos/
    README.txt                      macOS-specific notes (no driver needed)
```

## Troubleshooting

**Device not detected on any platform:**
- Try a different USB cable (some are charge-only, without data lines)
- Try a different USB port (avoid hubs if possible)
- Check that the device LED indicates power

**Windows — sensor shows as "Unknown Device":**
- Run `install.bat` as Administrator
- Open Device Manager, right-click the unknown device, and select "Update driver" pointing to the `drivers/windows/` directory

**Linux — "Permission denied" when accessing USB:**
- Verify udev rules are installed: `ls /etc/udev/rules.d/99-openmotion.rules`
- Check group membership: `groups` (should include `plugdev`)
- Log out and back in after running `install.sh`
- As a quick test: `sudo chmod 666 /dev/bus/usb/XXX/YYY`

**Linux — Console not appearing as `/dev/ttyACM*`:**
- Load the CDC ACM kernel module: `sudo modprobe cdc_acm`
- Check `dmesg | tail` after plugging in the device
