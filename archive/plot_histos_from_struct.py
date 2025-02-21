import numpy as np
import matplotlib.pyplot as plt

# Define struct sizes
USART_PACKET_LENGTH = 4100
SPI_PACKET_LENGTH = 4096

# Read the hex file
file_path = "memdump.txt"  # Update this with your actual filename
with open(file_path, "r") as f:
    hex_data = f.read().strip().replace("\n", "").replace(" ", "")  # Remove spaces & newlines

# Convert hex string to byte array
byte_data = bytes.fromhex(hex_data)

# Validate expected size
expected_size = (USART_PACKET_LENGTH * 4) + (SPI_PACKET_LENGTH * 4)
if len(byte_data) != expected_size:
    raise ValueError(f"Unexpected file size: {len(byte_data)} bytes (expected {expected_size} bytes)")

# Extract buffers
offset = 0
buffers = {}

for i in range(8):
    length = USART_PACKET_LENGTH if i in {0, 2, 3, 4} else SPI_PACKET_LENGTH
    buffers[f'cam{i}_buffer'] = np.frombuffer(byte_data[offset:offset + length], dtype=np.uint8)
    offset += length

# Plot histograms
plt.figure(figsize=(10, 12))
for i, (name, values) in enumerate(buffers.items()):
    plt.subplot(8, 1, i + 1)
    plt.bar(range(len(values)), values, width=1, color='blue')
    plt.title(name)
    plt.ylabel("Frequency")
    plt.xticks([])

plt.xlabel("Byte Index")
plt.tight_layout()
plt.show()
