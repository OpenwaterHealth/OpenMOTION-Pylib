# Overview

This repository contains the code used to interface with an OpenMotion sensor module. The sensor module runs the code contained in the `open-motion-aggregator` repository on an STM32H7 processor. The programs here communicates with the aggregator board's MCU over a serial connection.

A library called `omotion` is imported in many of the python scripts listed here to aid communication with the Sensor Module.

# Getting started
1. Install requirements.txt (`pip install -r requirements.txt`)
2. Plug in your aggregator module. Please wait 10 seconds for it to boot up before continuing.
3. Run `python multicam_setup.py` - this will flash each camera sensor one by one. Alternatively, you may flash just a single camera sensor by usising `python flash_camera.py 1` - this will flash just camera 1
4. Run `python monitor.py 1` - this will flash the camera with a few parameters (test modes, exposure times, gain settings, etc), start the camera streaming, start the frame sync generating, and then put the cameras into streaming mode. It will then recieve the histogram data for the defined number of seconds then close down. Modify the parameters at the top of this file if you want to adjust the gain, exposure time, etc. Change the number in the command line arguments to change the camera you'd like to interrogate. Cameras are numbered 1-8 and correspond to J1-J8 on the aggregator board.


