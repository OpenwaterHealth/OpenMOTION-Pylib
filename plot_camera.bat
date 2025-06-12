@REM del histogram.bin
@REM del histogram.csv

python scripts\test_sensor_if.py

python scripts\test_receive_multi_frame.py

python data-processing/parse_data_v2.py

python data-processing/plot_histo_average.py

@REM python data-processing/plot_all_histo_average.py

@REM python data-processing/plot_single_spectrogram.py

@REM python data-processing/plot_all_spectrogram.py 

@REM python data-processing/plot_single_histogram.py --csv histogram.csv --cam 0  --row 100 --style bar
