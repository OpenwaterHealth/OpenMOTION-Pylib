del histogram.bin
del histogram.csv

:: check if the current directory is in PYTHONPATH
if "%PYTHONPATH%"=="" (
    echo PYTHONPATH is not set, setting it to the current directory.
    set PYTHONPATH=%cd%
) else (
    echo PYTHONPATH is currently set to: %PYTHONPATH%
    echo %PYTHONPATH% | findstr /i "%cd%" >nul
    if errorlevel 1 (
        echo Current directory not in PYTHONPATH, adding it.
        set PYTHONPATH=%cd%;%PYTHONPATH%
    ) else (
        echo Current directory is already in PYTHONPATH.
    )
)

python scripts\flash_sensors.py --camera-mask 0x01

python scripts\camera_tester.py 01