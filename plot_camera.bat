del histogram.bin
del histogram.csv

python scripts\flash_sensors.py --camera-mask 99
python scripts\set_trigger_laser.py
python scripts\capture_data.py --camera-mask 99 --subject-id Test --duration 30


@REM python scripts\test_receive_multi_frame.py 66 120

@rem python scripts\test_receive_multi_frame_console.py 99 30

python data-processing/parse_data_v2.py

python data-processing/check_csv.py

@REM python data-processing/plot_histo_average.py

python data-processing/plot_all_histo_average.py

@REM python data-processing/plot_single_spectrogram.py

@REM python data-processing/plot_all_spectrogram.py 

@REM python data-processing/plot_single_histogram.py --csv histogram.csv --cam 0  --row 100 --style bar
