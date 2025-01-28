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

    async def flash_camera(self, state, module, camera, bitstream= "HistoFPGAFw_impl1.bit", verbose=False):
        if(state["sensors"] is None):
            print("No console found")
            return
        module = int(module)
        camera = int(camera)
        sensor_module = state["sensors"][module]
        print("Flashing Camera Module " + str(module+1) + " Camera " + str(camera+1))

        # Calculate CRC of the specified file
        file_crc = self.calculate_file_crc(bitstream)
        # print(f"CRC16 of file {FILE_NAME}: {hex(file_crc)}")
        
        await sensor_module.switch_camera(camera)
        time.sleep(1)

        if(verbose): print("FPGA Configuration Started")
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

        if(verbose): print("FPGA Configuration Completed")

        if(verbose): print("Camera Configuration Started")
        r = await sensor_module.camera_configure_registers()
        r = await sensor_module.fpga_soft_reset()
        if(verbose): print("Camera Configuration Completed")        

    async def flash_all(self, state):
        if(state["sensors"] is None):
            print("No sensors found")
            return
        for sensor_id in range(0,len(state["sensors"])):
            for camera_id in range(0, 8):
                print(f"Flashing Camera {camera_id+1} on Sensor {sensor_id+1}")
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
        else:
            print("Using console FSIN")
        
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
            await sensor_module.camera_stream_off()
            print("Exiting the program.")


    async def system_info(self, state):
        if(state["console"] is None):
            print("No console found")
            return
        else:
            print("Console found")
            console = state["console"]
            r = await console.version()
            print("FW Version: " + r.data.hex())

        if(state["sensors"] is None):
            print("No console found")
            return
        else:
            print("Sensors found")
            for sensor_module in state["sensors"]:
                print("Sensor found")

                r = await sensor_module.version()
                print("FW Version: " + r.data.hex())
                for i in range(1,9):
                    await sensor_module.switch_camera(i)
                    # time.sleep(.01)
                    temp = await sensor_module.read_camera_temp()
                    print("Camera ", str(i), " temperature: ", temp," C")
                    # time.sleep(.01)
    async def toggle_camera_stream(self, state, sensor_id, camera_id):
        sensor_id = int(sensor_id)
        camera_id = int(camera_id)
        print("Toggling Camera " + str(camera_id+1) + " on Sensor " + str(sensor_id+1))
        if(state["sensors"] is None):
            print("No sensors found")
            return
        else:
            sensor_module = state["sensors"][sensor_id]
            await sensor_module.toggle_camera_stream(camera_id)
    async def stream_all(self, state, time_s):
        delay_time = .1
        use_console_fsin = True
        if(state["sensors"] is None):
            print("No sensors found")
            return
        if(state["console"] is None):
            print("No console found")
            print("Falling back to Aggregator board FSIN")
            use_console_fsin = False
        else:
            print("Using console FSIN")
            
            json_trigger_data = {
                "TriggerFrequencyHz": 40,
                "TriggerPulseWidthUsec": 12500,
                "LaserPulseDelayUsec": 100,
                "LaserPulseWidthUsec": 200
            }
            console = state["console"]
            await console.set_trigger(data=json_trigger_data)
        
        console = state["console"]
        time.sleep(.1)

        for sensor_module in state["sensors"]:
            if(use_console_fsin): await sensor_module.camera_fsin_ext_on()    

            await sensor_module.enable_i2c_broadcast()
            r = await sensor_module.camera_stream_on()    

        if(use_console_fsin):
            r = await console.start_trigger()


        print("Camera Stream on")
        
        try:
            print("Streaming for " + str(time_s) + " seconds")
            time.sleep(time_s)
        finally:
            print("FSIN Off")
            
            for sensor_module in state["sensors"]:
                await sensor_module.camera_stream_off()
                # await sensor_module.i2c_broadcast_off()
                if(not use_console_fsin): await sensor_module.camera_fsin_off()
            
            if(use_console_fsin):
                await console.stop_trigger()
            
            time.sleep(delay_time*3)
            print("Stream Off")
            await sensor_module.camera_stream_off()
            print("Exiting the program.")

    async def macro(self, state):
        macro_num = 0
        if(macro_num==0):
            cameras_to_test = [5,6,7]
            for camera in cameras_to_test:
                await self.flash_camera(state, 0, camera)
                await self.toggle_camera_stream(state, 0, camera)
            
            await self.stream_all(state, 1)
            

    # systen info
    # r = await sensor_module.version()   
