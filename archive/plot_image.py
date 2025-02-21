import numpy as np
import matplotlib.pyplot as plt

def read_10bit_raw(file_path, width, height):
    """
    Reads a 10-bit packed monochrome RAW file and extracts pixel data.

    Parameters:
        file_path (str): Path to the .raw file.
        width (int): Image width in pixels.
        height (int): Image height in pixels.

    Returns:
        np.ndarray: 2D array of pixel values.
    """
    # Open and read the binary file
    with open(file_path, 'rb') as f:
        raw_data = np.fromfile(f, dtype=np.uint8)
    
    # Calculate the expected number of bytes: 5 bytes store 4 pixels
    expected_bytes = (width * height * 10) // 8
    if len(raw_data) != expected_bytes:
        print(f"Warning: Expected {expected_bytes} bytes, but got {len(raw_data)} bytes.")
    
    # Unpack the 10-bit packed data
    packed_data = raw_data.reshape(-1, 5)  # Group into chunks of 5 bytes
    pixel_values = []

    for chunk in packed_data:
        # Combine bytes into 10-bit values
        p0 = (chunk[0] << 2) | (chunk[1] >> 6)
        p1 = ((chunk[1] & 0x3F) << 4) | (chunk[2] >> 4)
        p2 = ((chunk[2] & 0x0F) << 6) | (chunk[3] >> 2)
        p3 = ((chunk[3] & 0x03) << 8) | chunk[4]
        pixel_values.extend([p0, p1, p2, p3])

    # Convert to numpy array and reshape to 2D
    pixel_array = np.array(pixel_values[:width * height], dtype=np.uint16).reshape(height, width)
    return pixel_array

def display_image_and_histogram(image, bit_depth=10):
    """
    Displays the image and its histogram.

    Parameters:
        image (np.ndarray): 2D array of pixel values.
        bit_depth (int): Bit depth of the image.
    """
    max_pixel_value = (2 ** bit_depth) - 1

    plt.figure(figsize=(12, 6))

    # Subplot 1: Display the image
    plt.subplot(1, 2, 1)
    plt.imshow(image, cmap='gray', aspect='auto', vmin=0, vmax=max_pixel_value)
    plt.title("10-bit Monochrome Image")
    plt.axis("off")

    # Subplot 2: Histogram
    plt.subplot(1, 2, 2)
    plt.hist(image.flatten(), bins=max_pixel_value + 1, range=(0, max_pixel_value), color='black', histtype='step')
    plt.title("Histogram of Image")
    plt.xlabel("Pixel Value")
    plt.ylabel("Frequency")

    plt.tight_layout()
    plt.show()


# Parameters
file_path = "gradient_pattern_cont.raw"  # Path to your .raw file
width = 1920            # Image width
height = 1280           # Image height

# Call the function
image = read_10bit_raw(file_path, width, height)
display_image_and_histogram(image)
