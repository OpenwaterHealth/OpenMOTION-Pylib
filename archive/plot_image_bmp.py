from PIL import Image
import numpy as np
import matplotlib.pyplot as plt

def display_bmp_image_and_histogram(file_path):
    """
    Reads a monochrome BMP image file, displays the image and its histogram.

    Parameters:
        file_path (str): Path to the BMP file.
    """
    try:
        # Open the BMP file
        image = Image.open(file_path).convert('L')  # Convert to grayscale (8-bit)
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        return
    except Exception as e:
        print(f"Error loading image: {e}")
        return
    
    # Convert image to numpy array
    image_array = np.array(image)

    # Display the image and histogram
    plt.figure(figsize=(12, 6))

    # Subplot 1: Display the image
    plt.subplot(1, 2, 1)
    plt.imshow(image_array, cmap='gray', aspect='auto')
    plt.title("Monochrome BMP Image")
    plt.axis("off")

    # Subplot 2: Histogram
    plt.subplot(1, 2, 2)
    plt.hist(image_array.flatten(), bins=256, range=(0, 255), color='black', histtype='step')
    plt.title("Histogram of Image")
    plt.xlabel("Pixel Value (0-255)")
    plt.ylabel("Frequency")

    plt.tight_layout()
    plt.show()

# Path to the BMP file
file_path = "soren_data_dec18/test_continuous_gradient.bmp"  # Replace with the path to your BMP file

# Call the function
display_bmp_image_and_histogram(file_path)
