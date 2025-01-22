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