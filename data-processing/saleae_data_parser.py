import csv
import matplotlib.pyplot as plt
import numpy as np

SALEAE_CSV = "saleae_data_dump_60s_spi_tp0.csv"
WORDS_PER_HISTO = 1025
BYTES_PER_HISTO = WORDS_PER_HISTO * 4

def parse_all_histograms(filename):
    # Read all MOSI bytes into a flat list
    bytes_list = []
    with open(filename, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            mosi_val = row['MOSI']
            if mosi_val and mosi_val.startswith("0x"):
                byte = int(mosi_val, 16)
                bytes_list.append(byte)

    total_histos = len(bytes_list) // BYTES_PER_HISTO
    print(f"Found {total_histos} full histograms in the capture.")

    histograms = []
    for i in range(total_histos):
        hist_bytes = bytes_list[i * BYTES_PER_HISTO : (i + 1) * BYTES_PER_HISTO]
        histogram = []
        for j in range(0, BYTES_PER_HISTO, 4):
            word = (
                hist_bytes[j] |
                (hist_bytes[j + 1] << 8) |
                (hist_bytes[j + 2] << 16) |
                (hist_bytes[j + 3] << 24)
            )
            histogram.append(word)
        histograms.append(histogram)

    return np.array(histograms)

def plot_spectrogram(histo_matrix):
    plt.figure(figsize=(12, 6))
    im = plt.imshow(
        histo_matrix,
        aspect='auto',
        interpolation='nearest',
        origin='lower',
        cmap='viridis'
    )
    plt.colorbar(im, label="Bin Count")
    plt.title("SPI Histogram Spectrogram")
    plt.xlabel("Bin Index")
    plt.ylabel("Frame Index")
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    histograms = parse_all_histograms(SALEAE_CSV)
    plot_spectrogram(histograms)
