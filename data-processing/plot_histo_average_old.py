import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

CSV_FILE = "20250207\histo_data_cam5_g3_light_0075um.csv"
NUM_BINS = 1024
FRAME_ID_MAX = 256  # Since frame_id wraps around at 255 -> 0

def plot_weighted_average(csv_path):
    df = pd.read_csv(csv_path)

    # Filter to the selected camera
    cam_df = df
    if cam_df.empty:
        print(f"No data found")
        return



    # Track logical frame index across frame_id rollover
    logical_indices = []
    last_frame_id = None
    rollover_count = 0

    for _, row in cam_df.iterrows():
        current_frame_id = row['id']
        if last_frame_id is not None:
            # Detect wraparound
            if current_frame_id < last_frame_id:
                rollover_count += 1
        logical_index = rollover_count * FRAME_ID_MAX + current_frame_id
        logical_indices.append(logical_index)
        last_frame_id = current_frame_id

    cam_df['logical_frame_index'] = logical_indices
    cam_df = cam_df.sort_values(by="logical_frame_index")

    # Extract histogram bin data
    histo_matrix = cam_df.iloc[:, 2:2 + NUM_BINS].to_numpy()

    # Zero out the last bin (bin 1023)
    histo_matrix[:, -1] = 0

    frame_ids = cam_df['logical_frame_index'].to_numpy()

    # Compute weighted averages
    bin_indices = np.arange(NUM_BINS)
    sums = np.sum(histo_matrix, axis=1)
    weighted_sums = np.dot(histo_matrix, bin_indices)
    weighted_avg = np.divide(weighted_sums, sums, out=np.zeros_like(sums, dtype=float), where=sums != 0)

    # Plot
    plt.figure(figsize=(10, 4))
    plt.plot(frame_ids, weighted_avg, marker='o', linestyle='-', markersize=3)
    plt.title(f" Weighted Average Over Time (bin 1023 zeroed)")
    plt.xlabel("Frame ID")
    plt.ylabel("Weighted Average Bin")
    plt.grid(True)
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    plot_weighted_average(CSV_FILE)
