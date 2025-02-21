from omotion import *
import csv
import numpy as np
import pandas as pd
import seaborn as sns

##
# Read the CSV file
data = pd.read_csv('histo_data.csv')

# Extract the frame identifier column
frame_id = data.iloc[:, 0]

# Extract the data columns
data_columns = data.iloc[:, 1:]


