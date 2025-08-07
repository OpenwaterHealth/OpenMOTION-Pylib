@echo off

python scripts\flash_sensors.py --camera-mask 0x11
python scripts\set_trigger_laser.py
python scripts\capture_data.py --camera-mask 0x11 --subject-id Test --duration 15

@REM Data Processing Scripts
@REM python data-processing/parse_data_v2.py
@REM python data-processing/check_csv.py

@REM Data Visualization Scripts

@REM python data-processing/plot_histo_average.py
@REM python data-processing/plot_all_histo_average.py
@REM python data-processing/plot_single_spectrogram.py
@REM python data-processing/plot_all_spectrogram.py 
@REM python data-processing/plot_single_histogram.py --csv histogram.csv --cam 0  --row 100 --style bar
