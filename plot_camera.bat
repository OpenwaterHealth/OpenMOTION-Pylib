del histogram.bin
del histogram.csv

:: check if the current directory is in PYTHONPATH
@echo off "%PYTHONPATH%"=="" (
    echo PYTHONPATH is not set, setting it to the current directory.
    set PYTHONPATH=%cd%;%PYTHONPATH%
) else (
    echo PYTHONPATH is currently set to: %PYTHONPATH%
)

python scripts\flash_sensors.py FF

python scripts\test_receive_multi_frame.py FF 30

python data-processing/parse_data_v2.py

python data-processing/check_csv.py

@REM python data-processing/plot_histo_average.py

python data-processing/plot_all_histo_average.py

@REM python data-processing/plot_single_spectrogram.py

@REM python data-processing/plot_all_spectrogram.py 

@REM python data-processing/plot_single_histogram.py --csv histogram.csv --cam 0  --row 100 --style bar
