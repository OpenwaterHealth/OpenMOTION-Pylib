import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation

# Load CSV file
csv_filename = "histo_data.csv"  # Change this to your CSV file
df = pd.read_csv(csv_filename)

# Extract relevant information
bin_columns = df.columns[3:]  # Histogram bins (assuming id, total, cam_id are first three columns)
df['id'] = df['id'].astype(int)  # Ensure id is an integer
df['cam_id'] = df['cam_id'].astype(int)  # Ensure cam_id is an integer

# Get unique cameras
camera_ids = sorted(df['cam_id'].unique())

# Sort data by time and camera ID
df = df.sort_values(by=['id', 'cam_id'])

# Normalize the time index
time_steps = sorted(df['id'].unique())  # Unique time steps
num_time_steps = len(time_steps)
fps = 1000 / 0.025  # Convert 0.025ms to FPS

# Create figure and axes
fig, axes = plt.subplots(len(camera_ids), 1, figsize=(10, 12), sharex=True)

# If there's only one camera, make axes a list
if len(camera_ids) == 1:
    axes = [axes]

# Initialize plots
lines = []
for ax, cam_id in zip(axes, camera_ids):
    ax.set_title(f"Camera {cam_id}")
    ax.set_xlim(0, len(bin_columns))  # X-axis range
    ax.set_ylim(0, df[bin_columns].values.max())  # Y-axis range
    line, = ax.plot([], [], lw=2)
    lines.append(line)

# Add a text annotation for the time step (id)
time_text = fig.text(0.85, 0.95, '', fontsize=14, bbox=dict(facecolor='white', alpha=0.8))

# Animation update function
def update(frame):
    time_step = time_steps[frame % num_time_steps]  # Loop time at 255
    time_text.set_text(f"Time: {time_step}")
    
    for ax, cam_id, line in zip(axes, camera_ids, lines):
        data = df[(df['id'] == time_step) & (df['cam_id'] == cam_id)]
        if not data.empty:
            histogram = data[bin_columns].values[0]
            line.set_data(np.arange(len(histogram)), histogram)
    
    return lines + [time_text]

# Create animation
ani = animation.FuncAnimation(fig, update, frames=num_time_steps, interval=25, blit=False)

plt.xlabel("Histogram Bin")
plt.tight_layout()
plt.show()
