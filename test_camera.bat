del histogram.bin
del histogram.csv

:: check if the current directory is in PYTHONPATH
@echo off "%PYTHONPATH%"=="" (
    echo PYTHONPATH is not set, setting it to the current directory.
    set PYTHONPATH=%cd%;%PYTHONPATH%
) else (
    echo PYTHONPATH is currently set to: %PYTHONPATH%
)

python scripts\flash_sensors.py 0F

python scripts\camera_tester.py 0F