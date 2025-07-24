import argparse
import time
import numpy as np
import threading
import queue
from PyQt6 import QtWidgets, QtCore
import pyqtgraph as pg
import sys
from omotion.Interface import MOTIONInterface

BIT_FILE = "bitstream/HistoFPGAFw_impl1_agg.bit"
NUM_BINS = 1024

histogram_queue = queue.Queue(maxsize=5)


# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts\test_receive_frame.py


BIT_FILE = "bitstream/HistoFPGAFw_impl1_agg.bit"

def parse_args():
    parser = argparse.ArgumentParser(description="MOTION Sensor Histogram Capture Test")
    parser.add_argument("--camera-id", type=int, default=0, help="Camera ID (0-7)")
    parser.add_argument("--test-pattern", type=int, default=4, help="Test Pattern ID (0=gradient, 1=solid, 2=squares, 3=grad, 4=disabled)")
    parser.add_argument("--manual-upload", action="store_true", help="Manually upload the FPGA bitstream")
    parser.add_argument("--save", action="store_true", help="Save histogram to file")
    parser.add_argument("--plot", action="store_true", help="Plot histogram")
    parser.add_argument("--live", action="store_true", help="Live streaming histogram display")
    parser.add_argument("--fps", type=int, default=1, help="Frames per second for live view")
    return parser.parse_args()


def plot_10bit_histogram(histogram_data, title="10-bit Histogram"):
    """
    Plots a 10-bit histogram (0-1023) from raw byte data.
    
    Args:
        histogram_data (bytearray): Raw histogram data from the sensor.
        title (str): Title for the plot (default: "10-bit Histogram").
    """
    import matplotlib.pyplot as plt
    try:
        # Convert bytearray to a numpy array of 16-bit integers (assuming little-endian)
        # Each histogram bin is 4 bytes (uint16)
        # hist_values = np.frombuffer(histogram_data, dtype='<u4')  # little-endian uint32
        
        # Verify expected length (1024 bins for 10-bit)
        if len(histogram_data) != 1024:
            print(f"Warning: Expected 1024 bins, got {len(histogram_data)}")
        
        # Plot the histogram
        plt.figure(figsize=(12, 6))
        plt.bar(range(len(histogram_data)), histogram_data, width=1.0)
        plt.title(title)
        plt.xlabel("Pixel Value (0-1023)")
        plt.ylabel("Count")
        plt.grid(True, linestyle='--', alpha=0.6)
        plt.show()
        
    except Exception as e:
        print(f"Error plotting histogram: {e}")

def live_histogram_viewer(data_queue, stop_event):
    app = QtWidgets.QApplication(sys.argv)
    win = pg.GraphicsLayoutWidget(show=True, title="Live 10-bit Histogram")
    win.setWindowTitle("Live Histogram Viewer")

    plot = win.addPlot(title="Live Histogram")
    bar_graph = plot.plot(pen=None, brush='g', width=1, stepMode=True)
    plot.setXRange(0, 1024)
    plot.setYRange(0, 1000)  # Adjust as needed for expected bin counts

    def update():
        try:
            while not data_queue.empty():
                bins = data_queue.get_nowait()
                if isinstance(bins, list):
                    bins = np.array(bins)
                bar_graph.setData(bins)
        except Exception as e:
            print(f"[Viewer] Error updating plot: {e}")

    timer = QtCore.QTimer()
    timer.timeout.connect(update)
    timer.start(1000 // 30)  # 30Hz update rate

    def on_close():
        stop_event.set()
        QtWidgets.QApplication.quit()

    win.closeEvent = lambda event: (on_close(), event.accept())

    app.exec()

def start_live_view(interface, camera_mask, fps):
    stop_event = threading.Event()
    q = queue.Queue(maxsize=5)

    def acquisition_loop():
        try:
            while not stop_event.is_set():
                if interface.sensor_module.camera_capture_histogram(camera_mask):
                    frame = interface.sensor_module.camera_get_histogram(camera_mask)
                    if frame:
                        frame = frame[:4096]
                        bins, _ = bytes_to_integers(frame)
                        if sum(bins) == 0:
                            print("Received empty histogram frame.")
                        else:
                            print("Sum of bins:", sum(bins))
                        q.put(bins)
                time.sleep(1.0 / fps)
        except Exception as e:
            print(f"Acquisition thread error: {e}")
        finally:
            stop_event.set()

    acquisition_thread = threading.Thread(target=acquisition_loop)
    acquisition_thread.start()

    # Run GUI in main thread
    live_histogram_viewer(q, stop_event)

    # Wait for acquisition to finish
    acquisition_thread.join()

def save_histogram_raw(histogram_data: bytearray, filename: str = "histogram.bin"):
    """Saves raw histogram bytes to a binary file."""
    try:
        with open(filename, "wb") as f:  # 'wb' = write binary
            f.write(histogram_data)
        print(f"Successfully saved raw histogram to {filename}")
    except Exception as e:
        print(f"Error saving histogram: {e}")

def bytes_to_integers(byte_array):
        # Check that the byte array is exactly 4096 bytes
        if len(byte_array) != 4096:
            raise ValueError("Input byte array must be exactly 4096 bytes.")
        # Initialize an empty list to store the converted integers
        integers = []
        hidden_figures = []
        # Iterate over the byte array in chunks of 4 bytes
        for i in range(0, len(byte_array), 4):
            bytes = byte_array[i:i+4]
            # Unpack each 4-byte chunk as a single integer (big-endian)
#            integer = struct.unpack_from('<I', byte_array, i)[0]
            # if(bytes[0] + bytes[1] + bytes[2] + bytes[3] > 0):
            #     print(str(i) + " " + str(bytes[0:3]))
            hidden_figures.append(bytes[3])
            integers.append(int.from_bytes(bytes[0:3],byteorder='little'))
        return (integers, hidden_figures)

def main():
    args = parse_args()
    camera_id = args.camera_id
    camera_mask = 1 << camera_id
    test_pattern = args.test_pattern
    enable_test_pattern = test_pattern != 4

    print("Starting MOTION Sensor Module Test Script...")
    interface = MOTIONInterface()
    
    try:

        # Check if console and sensor are connected
        console_connected, sensor_connected = interface.is_device_connected()

        if console_connected and sensor_connected:
            print("MOTION System fully connected.")
        else:
            print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR: {sensor_connected}')
            
        if not sensor_connected:
            print("Sensor Module not connected.")
            return

        # Ping Test
        response = interface.sensor_module.ping()
        print("Ping successful." if response else "Ping failed.")

        # Get Firmware Version
        version = interface.sensor_module.get_version()
        print(f"Firmware Version: {version}")

        print("Programming camera FPGA")
        if not interface.sensor_module.program_fpga(camera_position=camera_mask, manual_process=False):
            print("Failed to enter sram programming mode for camera FPGA.")
            return
        
        if(enable_test_pattern):
            print ("Programming camera sensor set test pattern.")
            if not interface.sensor_module.camera_configure_test_pattern(camera_mask, test_pattern):
                print("Failed to set grayscale test pattern for camera FPGA.")
                return
        else:
            print ("Programming camera sensor registers.")
            if not interface.sensor_module.camera_configure_registers(camera_mask):
                print("Failed to configure default registers for camera FPGA.")
                return


        if args.live:
            print(f"Starting live view at {args.fps} FPS. Press Ctrl+C or close the window to exit.")
            start_live_view(interface, camera_mask, args.fps)
        else:
            print("Capture histogram frame.")
            if not interface.sensor_module.camera_capture_histogram(camera_mask):
                print("Failed to capture histogram frame.")
                return
            else:
                print("Get histogram frame.")
                histogram = interface.sensor_module.camera_get_histogram(camera_mask)
                if histogram is None:
                    print("Histogram frame is None.")
                    return
                
                print("Histogram frame received successfully.")
                print("Histogram frame length: " + str(len(histogram)))
                histogram = histogram[0:4096]
                (bins, hidden_numbers) = bytes_to_integers(histogram)
                #print out sum of bins
                print("Sum of bins: " + str(sum(bins)))
                print("Bins: " + str(bins))
                print("Frame ID: " + str(hidden_numbers[1023]))

                if args.save:
                    save_histogram_raw(histogram, filename=f"camera_{camera_id}_histogram.bin")
                if args.plot:
                    plot_10bit_histogram(bins, title=f"Camera {camera_id} Histogram")

    finally:
        interface.sensor_module.disconnect()
        print("Sensor Module Test Completed.")


if __name__ == "__main__":
    main()