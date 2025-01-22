#!/usr/bin/env python3

import argparse
from cli_callbacks import Callbacks
import asyncio
from omotion import *
import json
import time

async def main():
    state = {
        "fsout_enabled": False,
        "fsin_enabled": False,
        "modules": {},
        "cameras": {},
    }

    parser = argparse.ArgumentParser(description="CLI Application")
    subparsers = parser.add_subparsers(title="Commands", dest="command")

    # Test Commands
    test_parser = subparsers.add_parser("test", help="Test-related commands")
    test_parser.add_argument("--enable_fsout", action="store_true", help="Enable FSOUT")
    test_parser.add_argument("--enable_fsin", action="store_true", help="Enable FSIN")
    test_parser.add_argument("--test_frame_sync", nargs=3, metavar=("frequency", "period","time_s"), type=int, help="Test frame sync")
    test_parser.add_argument("--test_laser_sync", nargs=2, metavar=("frequency", "period"), type=int, help="Test laser sync")
    test_parser.add_argument("--flash_fpga", nargs=2, metavar=("module", "camera"), help="Flash FPGA")
    test_parser.add_argument("--flash_camera", nargs=2, metavar=("module", "camera"), help="Flash camera")
    test_parser.add_argument("--flash_all", action="store_true", help="Flash all devices")
    test_parser.add_argument("--monitor", nargs=2, metavar=("module_id", "camera_id"), type=int, help="Monitor camera")

    # System Information Commands
    sys_info_parser = subparsers.add_parser("system_information", help="System information commands")
    sys_info_parser.add_argument("--all", action="store_true", help="Get all system information")
    sys_info_parser.add_argument("--console", action="store_true", help="Get console information")
    sys_info_parser.add_argument("--modules", action="store_true", help="Get module information")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    # Connect to console
    console = list_vcp_with_vid_pid(CONSOLE_VID, CONSOLE_PID)
    if not console:
        print("No console found")
        exit()
    else:
        print("Console found at port: ", console[0])
        state["console_uart"] = UART(console[0], timeout=5)
        state["console"] = CTRL_IF( state["console_uart"] )

    # Connect to sensors
    sensor_comm_ports = list_vcp_with_vid_pid(SENSOR_VID, SENSOR_PID)
    if not sensor_comm_ports:
        print("No sensor found")
        state["sensors"] = None
        state["sensor_uarts"] = None
    elif(len(sensor_comm_ports) > 0):
        print("Sensor found at ports: ", sensor_comm_ports)
        state["sensor_uarts"] = [UART(sensor_comm_port) for sensor_comm_port in sensor_comm_ports]
        state["sensors"] = [CTRL_IF(sensor_uart) for sensor_uart in  state["sensor_uarts"]]
        
    # Process commands
    callbacks = Callbacks()
    if args.command == "test":
        if args.test_frame_sync:
            frequency, period, time_s = args.test_frame_sync
            await callbacks.test_frame_sync(state, frequency, period, time_s)
        if args.flash_camera:
            module, camera = args.flash_camera
            await callbacks.flash_camera(state, module, camera)
        if args.flash_all:
            await callbacks.flash_all(state)
        if args.monitor:
            module_id, camera_id = args.monitor
            await callbacks.monitor(state, module_id, camera_id)
        # not implemented yet
        if args.test_laser_sync:
            frequency, period = args.test_laser_sync
            callbacks.test_laser_sync(state, frequency, period)
        if args.enable_fsout:
            callbacks.enable_fsout(state)
        if args.enable_fsin:
            callbacks.enable_fsin(state)

    elif args.command == "system_information":
        if args.all:
            callbacks.get_system_info_all(state)
        if args.console:
            callbacks.get_system_info_console(state)
        if args.modules:
            callbacks.get_system_info_modules(state)

    # Close connections
    if(state["console_uart"] is not None): state["console_uart"].close()
    if(state["sensor_uarts"] is not None):
        for sensor in state["sensor_uarts"]: sensor.close()

if __name__ == "__main__":
    asyncio.run(main())