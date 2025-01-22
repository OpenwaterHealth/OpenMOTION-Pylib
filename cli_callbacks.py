import asyncio
from omotion import *
import json
import time

class Callbacks: 
    def __init__(self):
        pass
    def enable_fsout(self, state):

        print("Enabling FSOUT")
        state["console"].write(b"fsout on\n")
        time.sleep(1)
        print("FSOUT enabled")
    
    async def test_frame_sync(self, state, frequency_hz, pulse_width_us, time_s, verbose=False):        
        if(state["console"] is None):
            print("No console found")
            return

        print("Set Sync Trigger")
        json_trigger_data = {
            "TriggerFrequencyHz": frequency_hz,
            "TriggerPulseWidthUsec": pulse_width_us,
            "LaserPulseDelayUsec": 100,
            "LaserPulseWidthUsec": 200
        }
        console = state["console"]
        print(json_trigger_data)
        r = await console.set_trigger(data=json_trigger_data)
        if(verbose): r.print_packet(full=True)

        print("Trigger Start")
        # Send and Recieve General ping command
        r = await console.start_trigger()
        # Format and print the received data in hex format
        if(verbose): r.print_packet(full=True)

        time.sleep(time_s)
        print("Trigger Stop")
        # Send and Recieve General ping command
        r = await console.stop_trigger()
        # Format and print the received data in hex format
        if(verbose): r.print_packet(full=True)

        print("Frame Sync test completed")

    # Function to read file and calculate CRC
    def calculate_file_crc(self,file_name):
        with open(file_name, 'rb') as f:
            file_data = f.read()
            crc = util_crc16(file_data)
            return crc        

    async def flash_camera(self, state, module, camera, bitstream= "HistoFPGAFw_impl1.bit"):
        if(state["sensors"] is None):
            print("No console found")
            return
        module = int(module)
        camera = int(camera)
        sensor_module = state["sensors"][module]
        print("Flashing Camera Module")

        # Calculate CRC of the specified file
        file_crc = self.calculate_file_crc(bitstream)
        # print(f"CRC16 of file {FILE_NAME}: {hex(file_crc)}")
        
        await sensor_module.switch_camera(camera)
        time.sleep(1)

        print("FPGA Configuration Started")
        r = await sensor_module.fpga_reset()       # Take cresetb hi for 250ms then low for 1sec
        r = await sensor_module.fpga_activate()    # send activation key
        time.sleep(.1)
        r = await sensor_module.fpga_on()          # set cresetb hi again (10ms delay)

        r = await sensor_module.fpga_id()
        r = await sensor_module.fpga_enter_sram_prog()
        r = await sensor_module.fpga_erase_sram()
        r = await sensor_module.fpga_status()

        r = await sensor_module.send_bitstream(filename=bitstream)

        r = await sensor_module.fpga_usercode()
        r = await sensor_module.fpga_status()
        r = await sensor_module.fpga_exit_sram_prog()

        print("FPGA Configuration Completed")

        print("Camera Configuration Started")
        r = await sensor_module.camera_configure_registers()
        r = await sensor_module.fpga_soft_reset()
        print("Camera Configuration Completed")        

    async def flash_all(self, state):
        if(state["sensors"] is None):
            print("No sensors found")
            return
        for sensor_id in range(0,len(state["sensors"])):
            for camera_id in range(1, 9):
                print(f"Flashing Camera {camera_id} on Sensor {sensor_id+1}")
                await self.flash_camera(state, sensor_id, camera_id)
        print("Flashing all Cameras Completed")

    async def monitor(self, state, module_id, camera_id, gain=1, exposure=2, test_pattern=-1, monitor_time=1, use_console_fsin=True ):
        delay_time = .1
        if(state["sensors"] is None):
            print("No console found")
            return
        module_id = int(module_id)
        camera_id = int(camera_id)
        sensor_module = state["sensors"][module_id]
        sensor_module_uart = state["sensor_uarts"][module_id]
        if(state["console"] is None):
            print("No console found")
            print("Falling back to Aggregator board FSIN")
            use_console_fsin = False
            return
        
        console = state["console"]
        print("Monitoring Camera " + str(camera_id) + " on Sensor " + str(module_id+1))
        
        await sensor_module.switch_camera(camera_id)
        time.sleep(1)

        print("Gain: " + str(gain))    
        await sensor_module.camera_set_gain(gain)

        print("camera set exposure to setting " + str(exposure))    
        await sensor_module.camera_set_exposure(exposure)

        if(use_console_fsin):
            r = await sensor_module.camera_fsin_ext_on()
            r = await console.start_trigger()
        else:
            await sensor_module.camera_fsin_ext_off()
            r = await sensor_module.camera_fsin_on()

        print("Camera Stream on")
        r = await sensor_module.camera_stream_on()    
        time.sleep(.1)
        
        try:
            await sensor_module_uart.start_telemetry_listener(timeout=monitor_time)
        finally:
            time.sleep(delay_time)
            print("FSIN Off")
            if(use_console_fsin):
                await console.stop_trigger()
            else:
                await sensor_module.camera_fsin_off()

            time.sleep(delay_time*3)
            print("Stream Off")
            await console.camera_stream_off()
            print("Exiting the program.")
    # systen info
    # r = await sensor_module.version()   
#        print("FW Version: " + r.data.hex())
