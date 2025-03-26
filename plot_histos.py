import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import sys

def visualize_histogram_csv(csv_path,show = False):
    # Load the CSV file
    df = pd.read_csv(csv_path)

    # Get up to 8 unique camera IDs
    cam_ids = sorted(df['cam_id'].unique())[:8]
    n_cams = len(cam_ids)
    
    # Create subplot grid: 4 rows x 2 columns
    fig, axs = plt.subplots(4, 2, figsize=(16, 12), constrained_layout=True)

    # Custom layout index map:
    # Left column: 0, 1, 2, 3
    # Right column: 7, 6, 5, 4
    axis_order = [axs[i][0] for i in range(4)] + [axs[i][1] for i in reversed(range(4))]
    # print(f"Axis order: {axis_order}")
    for idx, cam_id in enumerate(cam_ids):
        ax = axis_order[idx]

        # Filter and sort the dataframe for this cam_id
        cam_df = df[df['cam_id'] == cam_id].sort_values(by='id')

        # Extract histogram data
        histo_data = cam_df.loc[:, '0':'1023'].to_numpy()

        # Extract mean and std deviation of histo_data where the mean of each histogram is the height of the histogram times the bin number divided by the sum of the histogram, averaged over all frames
        mean = np.sum(histo_data * np.arange(1024)) / np.sum(histo_data)
        std = np.sqrt(np.sum(histo_data * (np.arange(1024) - mean) ** 2) / np.sum(histo_data))

        # Plot
        vmin = 0
        vmax = np.percentile(histo_data, 99)  # or set a fixed value like 100

        im = ax.imshow(histo_data, aspect='auto', interpolation='nearest', cmap='viridis',
                       extent=[0, 1023, cam_df['id'].max(), cam_df['id'].min()],
                       vmin=vmin, vmax=vmax)
        ax.set_title(f'Camera {cam_id+1}\nMean: {mean:.2f}, Std: {std:.2f}')
        ax.set_xlabel('Histogram Bin')
        ax.set_ylabel('Frame ID')
        fig.colorbar(im, ax=ax, orientation='vertical', label='Pixel Count')

    # Turn off any unused axes
    for i in range(n_cams, 8):
        axis_order[i].axis('off')

    plt.suptitle(f'Histogram Visualization\n {csv_path}', fontsize=16)

    # put a large red x in a circle in the bottom center of the figure
    plt.figtext(0.5, 0.03, 'X', color='red', ha='center', va='center', fontsize=24, fontweight='bold', bbox=dict(facecolor='white', edgecolor='red', boxstyle='circle'))

    # plt.text(512, -1, 'X', color='red', ha='center', va='center', fontsize=24, fontweight='bold', bbox=dict(facecolor='white', edgecolor='red', boxstyle='circle'))
    if(show):
        plt.show()
    else:
        #save as png and name the png the same as the csv minus the .csv extension
        plt.savefig(csv_path[:-4] + '.png')
    plt.close()


#if there are command line arguments, assign the first one to csv path
if len(sys.argv) > 1:
    csv_path = sys.argv[1]
else:
    csv_path = "histo_data.csv" 
#if --show is in the command line arguments, set show to true
show = False
if "--show" in sys.argv:
    show = True
visualize_histogram_csv(csv_path, show=show)
