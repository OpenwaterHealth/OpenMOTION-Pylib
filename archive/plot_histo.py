import os
import glob
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.animation as animation

# Get the most recently captured .csv file in the directory
csv_files = glob.glob('*.csv')
latest_csv_file = max(csv_files, key=os.path.getctime)

# Read the data from the .csv file
data = pd.read_csv(latest_csv_file)

# Create a figure and axis for the plot
fig, ax = plt.subplots()

# Function to update the plot for each frame of the animation
def update(frame):
    ax.clear()
    ax.plot(data['x'], data['y'])  # Replace 'x' and 'y' with the column names in your .csv file
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_title('Animated Plot')

# Create the animation
ani = animation.FuncAnimation(fig, update, frames=len(data), interval=200)

# Display the animated plot
plt.show()