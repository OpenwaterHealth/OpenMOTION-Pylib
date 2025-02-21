import serial
import time

# Configuration
SERIAL_PORT = "COM28"  # Change to your port (e.g., "/dev/ttyUSB0" for Linux)
BAUD_RATE = 250000    # Adjust to match the transmitter's settings
PACKET_SIZE = 32728   # Expected packet size
TIMEOUT = 1           # Serial read timeout in seconds

def receive_serial_data():
    with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=TIMEOUT) as ser:
        print(f"Listening on {SERIAL_PORT} at {BAUD_RATE} baud...")

        total_bytes = 0
        packet_count = 0
        packet_timestamps = []  # Stores timestamps of received packets
        start_time = time.time()

        while True:
            packet_start = time.time()
            data = ser.read(PACKET_SIZE)  # Read the expected packet size
            packet_end = time.time()

            if len(data) == PACKET_SIZE:
                total_bytes += len(data)
                packet_count += 1
                packet_timestamps.append(packet_end)  # Store the packet arrival time

                # Calculate packet time and rate
                elapsed = packet_end - packet_start
                rate_mbps = (len(data) * 8) / (elapsed * 1e6) if elapsed > 0 else 0

                print(f"Packet {packet_count}: {len(data)} bytes received in {elapsed:.6f} sec | {rate_mbps:.2f} Mbps")

            # Stop after 10 seconds
            if time.time() - start_time > 10:
                break

        # Calculate average time between packets
        if len(packet_timestamps) > 1:
            time_diffs = [
                packet_timestamps[i] - packet_timestamps[i - 1]
                for i in range(1, len(packet_timestamps))
            ]
            avg_time_between_packets = sum(time_diffs) / len(time_diffs)
        else:
            avg_time_between_packets = 0  # Not enough data to calculate

        total_time = time.time() - start_time
        avg_rate_mbps = (total_bytes * 8) / (total_time * 1e6)

        print("\n--- Summary ---")
        print(f"Total: {packet_count} packets, {total_bytes} bytes in {total_time:.2f} sec")
        print(f"Average speed: {avg_rate_mbps:.2f} Mbps")
        print(f"Average time between packets: {avg_time_between_packets:.6f} sec")

if __name__ == "__main__":
    receive_serial_data()
