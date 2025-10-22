import sys
import time
from omotion.Interface import MOTIONInterface

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\demo.py

HELP = """\
Commands:
  get                 - Read current TEC setpoint (volts)
  set <volts>         - Set TEC setpoint to <volts> and read back
  read <channel>      - Read TEC ADC voltage on specified channel (0-3 or 4 for all)
  help                - Show this help
  quit / exit         - Leave the console
"""

def ensure_console(interface):
    if not hasattr(interface, "console_module"):
        raise RuntimeError("Interface has no 'console_module'.")
    return interface.console_module

def main():
    print("Starting MOTION TEC Console…")

    # Acquire interface + connection state
    interface, console_connected, left_sensor, right_sensor = MOTIONInterface.acquire_motion_interface()

    if console_connected and left_sensor and right_sensor:
        print("MOTION System fully connected.")
    else:
        print(f"MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR (LEFT,RIGHT): {left_sensor}, {right_sensor}")

    if not console_connected:
        print("Console Module not connected.")
        sys.exit(1)

    console = ensure_console(interface)

    # Quick ping
    print("\nPinging Console Module…")
    ok = False
    try:
        ok = bool(console.ping())
    except Exception as e:
        print(f"Ping failed: {e}")
    print("Ping successful." if ok else "Ping failed (continuing).")

    print("\nType 'help' for commands.\n")

    # Initial temperature readout
    try:
        mcu, safety, ta = console.get_temperatures()
        print(f"Temps → MCU: {mcu:.2f} °C | Safety: {safety:.2f} °C | TA: {ta:.2f} °C")
    except Exception as e:
        print(f"Temperature read failed: {e}")


    while True:
        try:
            line = input("tec> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not line:
            continue

        cmd, *args = line.split()

        if cmd.lower() in ("quit", "exit"):
            print("Bye.")
            break

        if cmd.lower() == "help":
            print(HELP)
            continue

        if cmd.lower() == "get":
            try:
                volts = console.tec_voltage()  # GET (no arg)
                print(f"Current TEC setpoint: {volts:.6f} V")
            except Exception as e:
                print(f"GET failed: {e}")
            continue

        if cmd.lower() == "set":
            if not args:
                print("Usage: set <volts>")
                continue
            try:
                target = float(args[0])
            except ValueError:
                print("Invalid number; try e.g. 'set 1.25'")
                continue

            try:
                console.tec_voltage(target)  # SET
                # Short pause in case firmware updates asynchronously
                time.sleep(0.02)
                readback = console.tec_voltage()  # GET
                print(f"Setpoint requested: {target:.6f} V; readback: {readback:.6f} V")
            except Exception as e:
                print(f"SET failed: {e}")
            continue

        if cmd.lower() == "read":
            if not args:
                print("Usage: read <channel>")
                continue
            try:
                cahannel = int(args[0])
            except ValueError:
                print("Invalid number; try e.g. 'read 1'")
                continue

            try:
                ch_volts = console.tec_adc(cahannel)  # read channel
                # Short pause in case firmware updates asynchronously     
                if cahannel == 4:
                    formatted = ", ".join(f"{v:.6f} V" for v in ch_volts)
                    print(f"CHANNELS 0-3: {formatted}") 
                else:          
                    print(f"CHANNEL {cahannel}: {ch_volts:.6f} V")
            except Exception as e:
                print(f"TEC ADC read failed: {e}")
            continue


        print("Unknown command. Type 'help' for a list of commands.")

if __name__ == "__main__":
    main()
