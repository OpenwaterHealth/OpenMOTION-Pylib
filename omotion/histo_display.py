import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import time

# Simulated data generation for a 10-bit histogram (values from 0 to 1023)
def generate_histogram():
    # Replace with actual data
    return np.random.randint(0, 100, size=1024)  # Example histogram data

# Initialize plot
fig, ax = plt.subplots()
bars = ax.bar(np.arange(1024), np.zeros(1024), color='black')
ax.set_ylim(0, 200)  # Adjust y-axis based on your expected max value

def update_histogram(frame):
    # Get new histogram data
    data = generate_histogram()
    for bar, value in zip(bars, data):
        bar.set_height(value)
    return bars

# Set up animation for 40 FPS
ani = animation.FuncAnimation(fig, update_histogram, frames=range(1000), interval=25, blit=True)

# Display plot
plt.show()