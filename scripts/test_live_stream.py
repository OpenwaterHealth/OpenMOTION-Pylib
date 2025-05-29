import sys
import time
import numpy as np
from omotion.Interface import MOTIONInterface
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QWidget, 
    QPushButton, QHBoxLayout
)
from PyQt6.QtCore import QThread, pyqtSignal
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts/test_live_stream.py


# Configuration
CAMERA_MASK = 0x01
HISTOGRAM_BINS = 1024
HISTOGRAM_BYTES = 4096  # 1024 bins * 4 bytes per bin
TARGET_FPS = 2

class HistogramCanvas(FigureCanvasQTAgg):
    def __init__(self, parent=None, width=8, height=6, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.ax = self.fig.add_subplot(111)
        self.bar_container = None
        super().__init__(self.fig)
        self.setParent(parent)
        self.current_max = 0
        self.ax.set_ylim(0, 100)  # Initial y-axis limit
        
    def update_plot(self, bins):
        """Update the histogram plot with new data"""
        current_max = max(bins) if bins else 1
        
        # Dynamically adjust y-axis limits
        if current_max > self.current_max * 1.2 or current_max < self.current_max * 0.8:
            self.current_max = current_max
            padding = max(10, self.current_max * 0.1)  # At least 10 units padding
            self.ax.set_ylim(0, self.current_max + padding)
        
        if self.bar_container is None:
            # Initial plot setup
            self.bar_container = self.ax.bar(range(len(bins)), bins, width=1.0)
            self.ax.set_title("Live Histogram")
            self.ax.set_xlabel("Pixel Value (0-1023)")
            self.ax.set_ylabel("Count")
            self.ax.grid(True, linestyle='--', alpha=0.6)
        else:
            # Update existing bars
            for rect, height in zip(self.bar_container.patches, bins):
                rect.set_height(height)
        
        self.draw()

class CaptureThread(QThread):
    new_frame = pyqtSignal(bytes)  # Signal emitted with new histogram data
    
    def __init__(self, interface):
        super().__init__()
        self.interface = interface
        self.running = False
        self.frame_delay = 1.0 / TARGET_FPS
        
    def run(self):
        """Main capture loop"""
        self.running = True
        while self.running:
            start_time = time.time()
            
            if self.interface.sensor_module.camera_capture_histogram(CAMERA_MASK):
                histogram = self.interface.sensor_module.camera_get_histogram(CAMERA_MASK)
                if histogram is not None:
                    # Convert bytearray to bytes before emitting
                    self.new_frame.emit(bytes(histogram[:HISTOGRAM_BYTES]))
            
            # Maintain target frame rate
            elapsed = time.time() - start_time
            if elapsed < self.frame_delay:
                time.sleep(self.frame_delay - elapsed)
    
    def stop(self):
        """Stop the capture thread"""
        self.running = False
        self.wait()

class MainWindow(QMainWindow):
    def __init__(self, interface):
        super().__init__()
        self.interface = interface
        self.capture_thread = None
        self.frame_count = 0
        
        self.setWindowTitle("MOTION Histogram Viewer")
        self.setGeometry(100, 100, 800, 600)
        
        # Create main widget and layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout()
        main_widget.setLayout(layout)
        
        # Create control buttons
        control_layout = QHBoxLayout()
        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        
        self.start_button.clicked.connect(self.start_capture)
        self.stop_button.clicked.connect(self.stop_capture)
        
        control_layout.addWidget(self.start_button)
        control_layout.addWidget(self.stop_button)
        layout.addLayout(control_layout)
        
        # Create histogram canvas
        self.canvas = HistogramCanvas(self)
        layout.addWidget(self.canvas)
        
        # Initialize interface
        if not self.init_sensor():
            self.start_button.setEnabled(False)
            self.statusBar().showMessage("Sensor initialization failed")
        
    def init_sensor(self):
        """Initialize the sensor interface"""
        # Check connections
        console_connected, sensor_connected = self.interface.is_device_connected()
        if not all([console_connected, sensor_connected]):
            print(f'MOTION System NOT Fully Connected. CONSOLE: {console_connected}, SENSOR: {sensor_connected}')
            if not sensor_connected:
                print("Sensor Module not connected.")
                return False
        
        # Ping Test
        if not self.interface.sensor_module.ping():
            print("Ping failed.")
            return False
        
        # FPGA Configuration
        print("Programming camera FPGA")
        if not self.interface.sensor_module.program_fpga(camera_position=CAMERA_MASK, manual_process=False):
            print("Failed to program FPGA.")
            return False
        
        print("Programming camera sensor registers.")
        if not self.interface.sensor_module.camera_configure_registers(CAMERA_MASK):
            print("Failed to configure registers.")
            return False
        
        return True
    
    def bytes_to_bins(self, byte_data):
        """Convert bytes to histogram bins"""
        bins = []
        for i in range(0, len(byte_data), 4):
            chunk = byte_data[i:i+4]
            bins.append(int.from_bytes(chunk[0:3], byteorder='little'))
        return bins[:HISTOGRAM_BINS]  # Ensure we only return 1024 bins
    
    def start_capture(self):
        """Start the histogram capture"""
        if self.capture_thread is None:
            self.capture_thread = CaptureThread(self.interface)
            self.capture_thread.new_frame.connect(self.update_histogram)
            self.capture_thread.start()
            
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.statusBar().showMessage("Capture started")
    
    def stop_capture(self):
        """Stop the histogram capture"""
        if self.capture_thread is not None:
            self.capture_thread.stop()
            self.capture_thread = None
            
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.statusBar().showMessage("Capture stopped")
    
    def update_histogram(self, frame_data):
        """Update the histogram display with new data"""
        self.frame_count += 1
        bins = self.bytes_to_bins(frame_data)
        self.canvas.update_plot(bins)
        self.statusBar().showMessage(f"Frame: {self.frame_count} | Max: {max(bins)}")
    
    def closeEvent(self, event):
        """Handle window close event"""
        self.stop_capture()
        self.interface.sensor_module.disconnect()
        event.accept()

def main():
    # Initialize MOTION interface
    interface = MOTIONInterface()
    
    # Create and run the application
    app = QApplication(sys.argv)
    window = MainWindow(interface)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()