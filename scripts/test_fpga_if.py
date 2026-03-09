import asyncio
import time
from omotion.GitHubReleases import GitHubReleases
from omotion.Interface import MOTIONInterface
import json

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_fpga_if.py


def main():
    print("Starting MOTION Console FPGA Test Script...")

    # Acquire interface + connection state
    interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}')
        
    if not console_connected:
        print("Console Module not connected.")
        exit(1)

    # Ping Test
    print("\n[1] Ping Console Module...")
    response = interface.console_module.ping()
    print("Ping successful." if response else "Ping failed.")

    # Read Firmware Version
    print("\n[2] Reading Firmware Version...")
    try:
        version = interface.console_module.get_version()
        print(f"Firmware Version: {version}")
    except Exception as e:
        print(f"Error reading version: {e}")


    # TA - mux_idx: 1; channel: 4; i2c_addr: 0x41 start=0x14}
    # Seed - mux_idx: 1; channel: 5; i2c_addr: 0x41 start=0x13}
    # Safety EE - mux_idx: 1; channel: 6; i2c_addr: 0x41 start=0x25}
    # Safety OPT - mux_idx: 1; channel: 7; i2c_addr: 0x41 start=0x25}
        
    # Read FPGA Test
    print("\n[3] Read data from FPGA register...")
    try:
        fpga_data, fpga_data_len = interface.console_module.read_i2c_packet(mux_index=1, channel=4, device_addr=0x41, reg_addr=0x14, read_len=4)
        if fpga_data is None:
            print(f"Read FPGA Failed")
        else:
            print(f"Read FPGA Success")
            print(f"Raw bytes: {fpga_data.hex(' ')}")  # Print as hex bytes separated by spaces

        print("Retrieve latest firmware versions")

        def _default_payload() -> dict:
            return {"name": "N/A", "browser_download_url": "", "created_at": ""}

        def _pick_latest_jed_asset(gh: GitHubReleases) -> dict:
            release = gh.get_latest_release()
            if not isinstance(release, dict):
                return _default_payload()

            assets = release.get("assets")
            if not isinstance(assets, list):
                try:
                    assets = gh.get_asset_list(release=release)
                except Exception:
                    assets = []

            if not isinstance(assets, list):
                assets = []

            jed_assets = []
            for a in assets:
                if not isinstance(a, dict):
                    continue
                name = str(a.get("name") or "")
                if name.lower().endswith(".jed"):
                    jed_assets.append(a)

            if not jed_assets:
                return _default_payload()

            # Prefer the newest .jed by created_at when available.
            jed_assets.sort(key=lambda a: str(a.get("created_at") or ""), reverse=True)
            best = jed_assets[0]
            return {
                "name": str(best.get("name") or "N/A"),
                "browser_download_url": str(best.get("browser_download_url") or ""),
                "created_at": str(best.get("created_at") or ""),
            }

        gh_ta = GitHubReleases("OpenwaterHealth", "openmotion-ta-fpga")
        gh_seed = GitHubReleases("OpenwaterHealth", "openmotion-seed-fpga")
        gh_safety = GitHubReleases("OpenwaterHealth", "openmotion-safety-fpga")

        payload = {
            "TA": _pick_latest_jed_asset(gh_ta),
            "SEED": _pick_latest_jed_asset(gh_seed),
            "SAFETY": _pick_latest_jed_asset(gh_safety),
        }

        print(f"Latest TA FPGA .jed asset: {payload['TA']}")
        print(f"Latest SEED FPGA .jed asset: {payload['SEED']}")
        print(f"Latest SAFETY FPGA .jed asset: {payload['SAFETY']}")


    except Exception as e:
        print(f"Error writing FPGA register: {e}")
    
if __name__ == "__main__":
    main()
