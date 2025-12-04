import logging
import struct
import time
import queue
from omotion.MotionComposite import MotionComposite
from omotion.config import OW_BAD_CRC, OW_BAD_PARSE, OW_CAMERA, OW_CAMERA_GET_HISTOGRAM, OW_CAMERA_SET_TESTPATTERN, OW_CAMERA_SINGLE_HISTOGRAM, OW_CAMERA_SET_CONFIG, OW_CMD, OW_CMD_ECHO, OW_CMD_HWID, OW_CMD_PING, OW_CMD_RESET, OW_CMD_TOGGLE_LED, OW_CMD_VERSION, OW_CMD_SET_FAN_CTL, OW_CMD_GET_FAN_CTL, OW_ERROR, OW_FPGA, OW_FPGA_ACTIVATE, OW_FPGA_BITSTREAM, OW_FPGA_ENTER_SRAM_PROG, OW_FPGA_ERASE_SRAM, OW_FPGA_EXIT_SRAM_PROG, OW_FPGA_ID, OW_FPGA_OFF, OW_FPGA_ON, OW_FPGA_PROG_SRAM, OW_FPGA_PROG_NVCM, OW_FPGA_RESET, OW_FPGA_STATUS, OW_FPGA_USERCODE, OW_IMU, OW_IMU_GET_ACCEL, OW_IMU_GET_GYRO, OW_IMU_GET_TEMP, OW_CAMERA_FSIN, OW_TOGGLE_CAMERA_STREAM, OW_CAMERA_STATUS, OW_CAMERA_FSIN_EXTERNAL, OW_UNKNOWN, OW_CAMERA_SWITCH, OW_I2C_PASSTHRU, OW_CAMERA_POWER_OFF, OW_CAMERA_POWER_ON, OW_CAMERA_POWER_STATUS
from omotion.i2c_packet import I2C_Packet
from omotion.utils import calculate_file_crc

logger = logging.getLogger("Sensor")

class MOTIONSensor:
    def __init__(self, uart: MotionComposite):
        """
        Initialize the MOTIONSensor Module.            
        """

        self.uart = uart

        if self.uart and not self.uart.async_mode:
            self.uart.check_usb_status()
            if self.uart.is_connected():
                logger.info("MOTION MOTIONSensor connected.")
            else:
                logger.info("MOTION MOTIONSensor NOT Connected.")    

    def is_connected(self)-> bool:        
        """
        Check if the MOTIONSensor is connected.   
        Returns True if connected, False otherwise.
        """
        if self.uart and self.uart.is_connected():
            return True
        else:
            return False
        
    def ping(self) -> bool:        
        """    
        Send a ping command to the MOTIONSensor and receive a response.
        Returns the response from the MOTIONSensor.
        """ 
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("Console Device not connected")

            logger.info("Send Ping to Device.")
            r = self.uart.comm.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_PING)
            self.uart.comm.clear_buffer()
            logger.info("Received Ping from Device.")
            # r.print_packet()

            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error sending ping")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def get_version(self) -> str:
        """
        Retrieve the firmware version of the Sensor Module.

        Returns:
            str: Firmware version in the format 'vX.Y.Z'.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while fetching the version.
        """
        try:
            if self.uart.demo_mode:
                return 'v0.1.1'

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return 'v0.0.0'

            r = self.uart.comm.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_VERSION)
            self.uart.comm.clear_buffer()
            # r.print_packet()
            if r.data_len == 3:
                ver = f'v{r.data[0]}.{r.data[1]}.{r.data[2]}'
            else:
                ver = 'v0.0.0'
            logger.info(ver)
            return ver
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def echo(self, echo_data = None) -> tuple[bytes, int]:
        """
        Send an echo command to the device with data and receive the same data in response.

        Args:
            echo_data (bytes): The data to send (must be a byte array).

        Returns:
            tuple[bytes, int]: The echoed data and its length.

        Raises:
            ValueError: If the UART is not connected.
            TypeError: If the `echo_data` is not a byte array.
            Exception: If an error occurs during the echo process.
        """
        try:
            if self.uart.demo_mode:
                data = b"Hello LIFU!"
                return data, len(data)

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return None, None

            # Check if echo_data is a byte array
            if echo_data is not None and not isinstance(echo_data, (bytes, bytearray)):
                raise TypeError("echo_data must be a byte array")

            r = self.uart.comm.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_ECHO, data=echo_data)
            self.uart.comm.clear_buffer()
            # r.print_packet()
            if r.data_len > 0:
                return r.data, r.data_len
            else:
                return None, None

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except TypeError as t:
            logger.error("TypeError: %s", t)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during echo process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def toggle_led(self) -> bool:
        """
        Toggle the LED on the Sensor Module.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while toggling the LED.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            r = self.uart.comm.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_TOGGLE_LED)
            self.uart.comm.clear_buffer()
            # r.print_packet()
            return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def set_fan_control(self, fan_on: bool) -> bool:
        """
        Set the fan control pin state on the Sensor Module.

        Args:
            fan_on (bool): True to turn fan ON (HIGH), False to turn fan OFF (LOW).

        Returns:
            bool: True if command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while setting fan control.
        """
        try:
            if self.uart.demo_mode:
                logger.info(f"Demo mode: Fan control set to {'ON' if fan_on else 'OFF'}")
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            # Send the SET_FAN_CTL command with reserved field set to 1 for ON, 0 for OFF
            reserved = 1 if fan_on else 0
            r = self.uart.comm.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_SET_FAN_CTL, reserved=reserved)
            self.uart.comm.clear_buffer()
            
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error setting fan control")
                return False
            else:
                logger.info(f"Fan control set to {'ON' if fan_on else 'OFF'}")
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during fan control: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def get_fan_control_status(self) -> bool:
        """
        Get the current fan control pin state from the Sensor Module.

        Returns:
            bool: True if fan is ON (HIGH), False if fan is OFF (LOW).

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while getting fan control status.
        """
        try:
            if self.uart.demo_mode:
                logger.info("Demo mode: Fan control status is ON")
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            # Send the GET_FAN_CTL command
            r = self.uart.comm.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_GET_FAN_CTL)
            self.uart.comm.clear_buffer()
            
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error getting fan control status")
                return False
            else:
                # The fan status is returned in the reserved field
                fan_status = r.reserved == 1
                logger.info(f"Fan control status: {'ON' if fan_status else 'OFF'}")
                return fan_status

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during fan status query: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def get_hardware_id(self) -> str:
        """
        Retrieve the hardware ID of the Sensor Module.

        Returns:
            str: Hardware ID in hexadecimal format.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while retrieving the hardware ID.
        """
        try:
            if self.uart.demo_mode:
                return bytes.fromhex("deadbeefcafebabe1122334455667788")

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return None

            r = self.uart.comm.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_HWID)
            self.uart.comm.clear_buffer()
            # r.print_packet()
            if r.data_len == 16:
                return r.data.hex()
            else:
                return None
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def imu_get_temperature(self) -> float:
        """
        Retrieve the temperature reading from the IMU on the Sensor Module.

        Returns:
            float: Temperature value in Celsius.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs or the received data length is invalid.
        """

        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False


            # Send the OW_IMU_GET_TEMP command
            r = self.uart.comm.send_packet(id=None, packetType=OW_IMU, command=OW_IMU_GET_TEMP)
            self.uart.comm.clear_buffer()
            # r.print_packet()

            # Check if the data length matches a float (4 bytes)
            if r.data_len == 4:
                # Unpack the float value from the received data (assuming little-endian)
                temperature = struct.unpack('<f', r.data)[0]
                # Truncate the temperature to 2 decimal places
                truncated_temperature = round(temperature, 2)
                return truncated_temperature
            else:
                raise ValueError("Invalid data length received for temperature")
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def imu_get_accelerometer(self) -> list[int]:
        """
        Retrieve raw accelerometer readings (X, Y, Z) from the IMU.

        Returns:
            list[int]: [x, y, z] accelerometer readings as signed 16-bit integers.

        Raises:
            ValueError: If UART is not connected or data is invalid.
            Exception: For unexpected issues.
        """
        try:
            if self.uart.demo_mode:
                return [0, 0, 0]

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                raise ValueError("UART is not connected")

            r = self.uart.comm.send_packet(id=None, packetType=OW_IMU, command=OW_IMU_GET_ACCEL)
            self.uart.comm.clear_buffer()

            if r.data_len == 6:
                x, y, z = struct.unpack('<hhh', r.data)  # 3 × int16_t (little endian)
                return [x, y, z]
            else:
                raise ValueError(f"Invalid data length: expected 6, got {r.data_len}")

        except ValueError as v:
            logger.error("ValueError in imu_get_accelerometer: %s", v)
            raise
        except Exception as e:
            logger.error("Unexpected error in imu_get_accelerometer: %s", e)
            raise

    def imu_get_gyroscope(self) -> list[int]:
        """
        Retrieve raw gyroscope readings (X, Y, Z) from the IMU.

        Returns:
            list[int]: [x, y, z] gyroscope readings as signed 16-bit integers.

        Raises:
            ValueError: If UART is not connected or data is invalid.
            Exception: For unexpected issues.
        """
        try:
            if self.uart.demo_mode:
                return [0, 0, 0]

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                raise ValueError("UART is not connected")

            r = self.uart.comm.send_packet(id=None, packetType=OW_IMU, command=OW_IMU_GET_GYRO)
            self.uart.comm.clear_buffer()

            if r.data_len == 6:
                x, y, z = struct.unpack('<hhh', r.data)  # 3 × int16_t (little endian)
                return [x, y, z]
            else:
                raise ValueError(f"Invalid data length: expected 6, got {r.data_len}")

        except ValueError as v:
            logger.error("ValueError in imu_get_gyroscope: %s", v)
            raise
        except Exception as e:
            logger.error("Unexpected error in imu_get_gyroscope: %s", e)
            raise

    def reset_camera_sensor(self, camera_position: int) -> bool:
        """
        Reset the camera sensor(s) at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).

        Returns:
            bool: True if the reset command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            r = self.uart.comm.send_packet(id=None, packetType=OW_FPGA, command=OW_FPGA_RESET, addr=camera_position)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error resetting camera sensor")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during reset_camera_sensor: %s", e)
            raise

    def activate_camera_fpga(self, camera_position: int) -> bool:
        """
        Activate the camera sensor(s) FPGA at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).

        Returns:
            bool: True if the FPGA command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            r = self.uart.comm.send_packet(id=None, packetType=OW_FPGA, command=OW_FPGA_ACTIVATE, addr=camera_position)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error activating fpga")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during activate_camera_fpga: %s", e)
            raise

    def enable_camera_fpga(self, camera_position: int) -> bool:
        """
        Enable the camera sensor(s) FPGA at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).

        Returns:
            bool: True if the FPGA command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            r = self.uart.comm.send_packet(id=None, packetType=OW_FPGA, command=OW_FPGA_ON, addr=camera_position)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error enabling fpga")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during enable_camera_fpga: %s", e)
            raise

    def disable_camera_fpga(self, camera_position: int) -> bool:
        """
        Disable the camera sensor(s) FPGA at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).

        Returns:
            bool: True if the FPGA command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            r = self.uart.comm.send_packet(id=None, packetType=OW_FPGA, command=OW_FPGA_OFF, addr=camera_position)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error disable fpga")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during disable_camera_fpga: %s", e)
            raise

    def check_camera_fpga(self, camera_position: int) -> bool:
        """
        Check the camera sensor(s) FPGA at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).

        Returns:
            bool: True if the FPGA command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            r = self.uart.comm.send_packet(id=None, packetType=OW_FPGA, command=OW_FPGA_ID, addr=camera_position)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error checking camera id")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during check_camera_fpga: %s", e)
            raise

    def enter_sram_prog_fpga(self, camera_position: int) -> bool:
        """
        Enter SRAM Programming mode for the camera sensor(s) FPGA at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).

        Returns:
            bool: True if the FPGA command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            r = self.uart.comm.send_packet(id=None, packetType=OW_FPGA, command=OW_FPGA_ENTER_SRAM_PROG, addr=camera_position)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error entering prog")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during enter_sram_prog_fpga: %s", e)
            raise

    def exit_sram_prog_fpga(self, camera_position: int) -> bool:
        """
        Exit SRAM Programming mode for the camera sensor(s) FPGA at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).

        Returns:
            bool: True if the FPGA command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            r = self.uart.comm.send_packet(id=None, packetType=OW_FPGA, command=OW_FPGA_EXIT_SRAM_PROG, addr=camera_position)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error entering prog")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during exit_sram_prog_fpga: %s", e)
            raise

    def erase_sram_fpga(self, camera_position: int) -> bool:
        """
        Erase SRAM for the camera sensor(s) FPGA at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).

        Returns:
            bool: True if the FPGA command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            r = self.uart.comm.send_packet(id=None, packetType=OW_FPGA, command=OW_FPGA_ERASE_SRAM, addr=camera_position, timeout=30)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error erasing SRAM")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during erase_sram_fpga: %s", e)
            raise

    def get_status_fpga(self, camera_position: int) -> bool:
        """
        Get Status of FPGA for the camera sensor(s) FPGA at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).

        Returns:
            bool: True if the FPGA command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            r = self.uart.comm.send_packet(id=None, packetType=OW_FPGA, command=OW_FPGA_STATUS, addr=camera_position)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error getting status")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during get_status_fpga: %s", e)
            raise

    def get_usercode_fpga(self, camera_position: int) -> bool:
        """
        Get usercode of FPGA for the camera sensor(s) FPGA at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).

        Returns:
            bool: True if the FPGA command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            r = self.uart.comm.send_packet(id=None, packetType=OW_FPGA, command=OW_FPGA_USERCODE, addr=camera_position)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error getting usercode")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during get_usercode_fpga: %s", e)
            raise

    def send_bitstream_fpga(self, filename=None) -> bool:
        """
        Sends a bitstream file to the FPGA via UART in blocks.

        Args:
            filename (str): The full path to the bitstream file.

        Returns:
            bool: True if the transfer is successful, False otherwise.
        """
        max_bytes_per_block = 1024
        block_count = 0
        total_bytes_sent = 0

        if filename is None:
            raise ValueError("Filename cannot be None")

        try:
            file_crc = calculate_file_crc(filename)
            logger.info(f"CRC16 of file: {hex(file_crc)}")

            with open(filename, "rb") as f:
                while True:
                    data = f.read(max_bytes_per_block)

                    if not data:                        
                        crc_bytes = file_crc.to_bytes(2, byteorder='big')
                        r = self.uart.comm.send_packet(
                            id=None,
                            packetType=OW_FPGA,
                            command=OW_FPGA_BITSTREAM,
                            addr=block_count,
                            reserved=1,  # Final block / EOF marker
                            data=crc_bytes
                        )
                        self.uart.comm.clear_buffer()

                        if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                            logger.error("Error sending final crc block")
                            return False
                        break

                    # Send actual data chunk
                    r = self.uart.comm.send_packet(
                        id=None,
                        packetType=OW_FPGA,
                        command=OW_FPGA_BITSTREAM,
                        addr=block_count,
                        reserved=0,
                        data=data
                    )
                    self.uart.comm.clear_buffer()

                    if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                        logger.error(f"Error sending block {block_count}")
                        return False

                    total_bytes_sent += len(data)
                    block_count += 1

            logger.info(f"Bitstream upload complete. Blocks sent: {block_count}, Total bytes: {total_bytes_sent}")
            return True

        except FileNotFoundError:
            logger.error(f"File {filename} not found.")
            return False
        except Exception as e:
            logger.error(f"Exception during bitstream send: {e}")
            return False

    def program_fpga(self, camera_position: int, manual_process: bool) -> bool:
        """
        Program FPGA for the camera sensor(s) FPGA at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).
            manual_process (bool): If True, the process is manual; otherwise, it's automatic.

        Returns:
            bool: True if the FPGA command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            r = self.uart.comm.send_packet(id=None, packetType=OW_FPGA, command=OW_FPGA_PROG_SRAM, addr=camera_position, reserved=1, timeout=60)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error programming FPGA")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during program_fpga: %s", e)
            raise

    def program_fpga_nvcm(self, camera_position: int) -> bool:
        """
        Program FPGA NVCM (non-volatile configuration memory) for the camera sensor(s) FPGA at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to program NVCM for cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to program (0x00 - 0xFF).

        Returns:
            bool: True if the NVCM programming command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            # NVCM programming can take longer, use extended timeout
            r = self.uart.comm.send_packet(id=None, packetType=OW_FPGA, command=OW_FPGA_PROG_NVCM, addr=camera_position, timeout=300)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error programming FPGA NVCM")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during program_fpga_nvcm: %s", e)
            raise

    def camera_configure_registers(self, camera_position: int) -> bool:
        """
        Program camera sensor(s) Registers at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).

        Returns:
            bool: True if the FPGA command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            r = self.uart.comm.send_packet(id=None, packetType=OW_CAMERA, command=OW_CAMERA_SET_CONFIG, addr=camera_position, timeout=60)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error configuring camera registers")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during camera_configure_registers: %s", e)
            raise

    def camera_configure_test_pattern(self, camera_position: int, test_pattern: int = 0 ) -> bool:
        """
        Set camera sensor(s) Test Pattern Registers at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).
            test_pattern (int): optional defaults to bars

        Returns:
            bool: True if the FPGA command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")
            
            if not (0x00 <= test_pattern <= 0x04):
                raise ValueError(f"test_pattern must be a byte (0x00 to 0x04), got {test_pattern:#04x}")

            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False
            
            r = self.uart.comm.send_packet(id=None, packetType=OW_CAMERA, command=OW_CAMERA_SET_TESTPATTERN, addr=camera_position, data=bytearray([test_pattern]), timeout=60)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error configuring camera test pattern")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during camera_configure_test_pattern: %s", e)
            raise

    def camera_capture_histogram(self, camera_position: int) -> bool:
        """
        Send Framesync to camera sensor(s) Registers at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).

        Returns:
            bool: True if the FPGA command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")
            
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return False

            r = self.uart.comm.send_packet(id=None, packetType=OW_CAMERA, command=OW_CAMERA_SINGLE_HISTOGRAM, addr=camera_position, reserved=0, timeout=15)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error capturing histogram")
                return False
            else:
                return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during camera_capture_histogram: %s", e)
            raise

    def camera_get_histogram(self, camera_position: int) -> bytearray:
        """
        Get Last Histogram from camera sensor(s) Registers at the specified position(s).

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, bit 1 = camera 1, ..., bit 7 = camera 7).
        For example, to reset cameras 0 and 3, use camera_position = 0b00001001 (0x09).

        Args:
            camera_position (int): Bitmask representing camera(s) to reset (0x00 - 0xFF).

        Returns:
            bytearray: Histogram data if the FPGA command was sent successfully, None otherwise.

        Raises:
            ValueError: If the UART is not connected or input is invalid.
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")
            
            if self.uart.demo_mode:
                return None

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return None

            r = self.uart.comm.send_packet(id=None, packetType=OW_CAMERA, command=OW_CAMERA_GET_HISTOGRAM, addr=camera_position, timeout=15)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error getting histogram")
                return None
            else:
                logger.debug(f"HIST Data Len: {len(r.data)}")
                return r.data

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise
        except Exception as e:
            logger.error("Exception during camera_get_histogram: %s", e)
            raise

    def get_camera_status(self, camera_position: int) -> dict[int, int] | None:
        """
        Get status flags from one or more cameras.

        Each bit in the `camera_position` byte represents one camera (bit 0 = camera 0, ..., bit 7 = camera 7).
        The status byte returned per camera includes:
            Bit 0: Peripheral READY (SPI/USART)
            Bit 1: Firmware programmed
            Bit 2: Configured
            Bit 7: Streaming enabled

        Args:
            camera_position (int): Bitmask of camera(s) to query (0x00–0xFF)

        Returns:
            dict[int, int] | None: Mapping of camera ID to status byte, or None on error

        Raises:
            ValueError: If input is invalid or UART not connected
        """
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")
            
            if self.uart.demo_mode:
                return {i: 0x07 for i in range(8) if (camera_position >> i) & 1}  # simulate READY + PROGRAMMED + CONFIGURED

            if not self.uart.is_connected():
                logger.error("Sensor Module not connected")
                return None

            r = self.uart.comm.send_packet(
                id=None,
                packetType=OW_CAMERA,
                command=OW_CAMERA_STATUS,
                addr=camera_position
            )
            self.uart.comm.clear_buffer()

            if r.packet_type == OW_ERROR or len(r.data) != 8:
                logger.error("Error getting camera status")
                return None

            # Each camera returns 1 byte of status, indexed by position
            return {
                i: r.data[i]
                for i in range(8)
                if (camera_position >> i) & 1
            }
        
        except Exception as e:
            logger.error("Exception in get_camera_status: %s", e)
            raise

    def soft_reset(self) -> bool:
        """
        Perform a soft reset on the Sensor device.

        Returns:
            bool: True if the reset was successful, False otherwise.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while resetting the device.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("Sensor Module not connected")

            r = self.uart.comm.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_RESET)
            self.uart.comm.clear_buffer()
            # r.print_packet()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error resetting device")
                return False
            else:
                return True
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def enable_aggregator_fsin(self) -> bool:
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("Sensor Module not connected")

            r = self.uart.comm.send_packet(id=None, packetType=OW_CAMERA, command=OW_CAMERA_FSIN,reserved=1)
            self.uart.comm.clear_buffer()
            # r.print_packet()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error enabling aggregator")
                return False
            else:
                return True
        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise
    def disable_aggregator_fsin(self) -> bool:
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("Sensor Module not connected")

            r = self.uart.comm.send_packet(id=None, packetType=OW_CAMERA, command=OW_CAMERA_FSIN,reserved=0)
            self.uart.comm.clear_buffer()
            # r.print_packet()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error enabling aggregator")
                return False
            else:
                return True
        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise

    def enable_camera(self, camera_position) -> bool:
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True 
            if not self.uart.is_connected():
                raise ValueError("Sensor Module not connected")
        
            r = self.uart.comm.send_packet(id=None, packetType=OW_CMD, reserved=1, command=OW_TOGGLE_CAMERA_STREAM, addr=camera_position)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error enabling camera")
                return False
            else:
                # calculate expected size by camera count
                return True
        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise

    def disable_camera(self, camera_position) -> bool:
        try:
            if not (0x00 <= camera_position <= 0xFF):
                raise ValueError(f"camera_position must be a byte (0x00 to 0xFF), got {camera_position:#04x}")

            if self.uart.demo_mode:
                return True 
            if not self.uart.is_connected():
                raise ValueError("Sensor Module not connected")
            r = self.uart.comm.send_packet(id=None, packetType=OW_CMD, reserved=0, command=OW_TOGGLE_CAMERA_STREAM, addr=camera_position,timeout=0.3)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error enabling camera")
                return False
            else:
                return True
        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise

    def enable_camera_fsin_ext(self) -> bool:
        """
        Enable the camera sensor(s) for external frame synchronization.

        Returns:
            bool: True if the command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("Sensor Module not connected")

            r = self.uart.comm.send_packet(id=None, packetType=OW_CAMERA, command=OW_CAMERA_FSIN_EXTERNAL, reserved=1)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error enabling camera FSIN")
                return False
            else:
                return True
        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise
    
    def disable_camera_fsin_ext(self) -> bool:
        """
        Disable the camera sensor(s) for external frame synchronization.

        Returns:
            bool: True if the command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("Sensor Module not connected")

            r = self.uart.comm.send_packet(id=None, packetType=OW_CAMERA, command=OW_CAMERA_FSIN_EXTERNAL, reserved=0)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error disabling camera FSIN")
                return False
            else:
                return True
        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise

    def camera_i2c_write(self, packet, packet_id=None):
        """
        Write data to a camera sensor's I2C register.
        
        Args:
            packet (I2CPacket): The I2C packet containing the device address, register address, and data to write.
            packet_id (int, optional): The ID for the packet. Defaults to None.

        Returns:
            bool: True if the command was sent successfully, False otherwise.
        
        Raises:
            ValueError: If the UART is not connected or if the packet is invalid.   
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("Sensor Module not connected")
            
            data = packet.register_address.to_bytes(2,'big') + packet.data.to_bytes(1,'big')
            response = self.uart.comm.send_packet(packetType=OW_I2C_PASSTHRU, command=packet.device_address, data=data)
        
            self.uart.comm.clear_buffer()
            if response.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error sending I2C write command")
                return False
            else:
                return True
        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise

    def camera_set_gain(self,gain,packet_id=None):
        ret = True
        gain = gain & 0xFF
        ret |= self.camera_i2c_write(I2C_Packet(device_address=0x36,register_address=0x3508,data=gain))
        time.sleep(0.05)

        ret |= self.camera_i2c_write(I2C_Packet(device_address=0x36,register_address=0x3509,data=0x00))  # this is for fine tuning and can be set to 0x00
        time.sleep(0.05)

        logger.info(f"Gain set to {gain}")
        return ret

    def camera_set_exposure(self,exposure_selection,us=None):
        ret = True
        exposures = [0x1F,0x20,0x2C,0x2D, 0x7a]
        exposure_byte = exposures[exposure_selection]
        # ;; exp=242.83us --> {0x3501,0x3502} = 0x001F
        # ;; exp=250.67us --> {0x3501,0x3502} = 0x0020
        # ;; exp=344.67us --> {0x3501,0x3502} = 0x002C
        # ;; exp=352.50us --> {0x3501,0x3502} = 0x002D
        # ;; exp=1098.00us --> {0x3501,0x3502} = 0x007A
        if us is not None:
            exposure_byte = int((us/9)) & 0xFF

        ret |= self.camera_i2c_write(I2C_Packet(device_address=0x36,register_address=0x3501,data=0x00))
        time.sleep(0.05)

        ret |= self.camera_i2c_write(I2C_Packet(device_address=0x36,register_address=0x3502,data=exposure_byte))
        time.sleep(0.05)
        exp_us = exposure_byte * 9
        logger.info(f"Exposure set to {exposure_byte} ({exp_us}us)")
        return ret
    
    def switch_camera(self, camera_id, packet_id=None):
        bytes_val = camera_id.to_bytes(1, 'big')
        response = self.uart.comm.send_packet(packetType=OW_CAMERA, command=OW_CAMERA_SWITCH, data=bytes_val)
        self.uart.comm.clear_buffer()
        return response

    def enable_camera_power(self,camera_mask: int) -> bool:
        """
        Enable power to the specified camera(s).

        Args:
            camera_mask (int): A bitmask representing the cameras to power on (0x01 to 0xFF).

        Returns:
            bool: True if the command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected.
        """
        try:
            if not (0x01 <= camera_mask <= 0xFF):
                raise ValueError(f"camera_mask must be between 0x01 and 0xFF, got {camera_mask:#04x}")

            if not self.uart.is_connected():
                raise ValueError("Sensor Module not connected")

            r = self.uart.comm.send_packet(id=None, packetType=OW_CAMERA, command=OW_CAMERA_POWER_ON, addr=camera_mask)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error enabling camera power")
                return False
            else:
                return True
        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise

    def disable_camera_power(self,camera_mask: int) -> bool:
        """
        Disable power to the specified camera(s).

        Args:
            camera_mask (int): A bitmask representing the cameras to power off (0x01 to 0xFF).

        Returns:
            bool: True if the command was sent successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected.
        """
        try:
            if not (0x01 <= camera_mask <= 0xFF):
                raise ValueError(f"camera_mask must be between 0x01 and 0xFF, got {camera_mask:#04x}")

            if not self.uart.is_connected():
                raise ValueError("Sensor Module not connected")

            r = self.uart.comm.send_packet(id=None, packetType=OW_CAMERA, command=OW_CAMERA_POWER_OFF, addr=camera_mask)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error disabling camera power")
                return False
            else:
                return True
        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise

    def get_camera_power_status(self) -> list:
        """
        Get power status for all cameras.

        Returns:
            list: A list of 8 booleans representing the power status of cameras 0-7.
                 True indicates the camera is powered on, False indicates it's powered off.

        Raises:
            ValueError: If the UART is not connected.
        """
        try:
            if not self.uart.is_connected():
                raise ValueError("Sensor Module not connected")

            # Query all cameras (0xFF mask)
            r = self.uart.comm.send_packet(id=None, packetType=OW_CAMERA, command=OW_CAMERA_POWER_STATUS, addr=0xFF)
            self.uart.comm.clear_buffer()
            if r.packet_type in [OW_ERROR, OW_BAD_CRC, OW_BAD_PARSE, OW_UNKNOWN]:
                logger.error("Error getting camera power status")
                return [False] * 8
            else:
                # Parse the response data - single byte mask where each bit represents power status for camera 0-7
                power_status = [False] * 8
                if r.data and len(r.data) >= 1:
                    power_mask = r.data[0]
                    for i in range(8):
                        power_status[i] = bool(power_mask & (1 << i))
                return power_status
        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise


    def disconnect(self):
        """
        Disconnect the UART and clean up.
        """
        if self.uart:
            logger.info("Disconnecting MOTIONSensor UART...")
            self.uart.disconnect()  
            self.uart = None

    def __del__(self):
        """
        Fallback cleanup when the object is garbage collected.
        """
        try:
            self.disconnect()
        except Exception as e:
            logger.warning("Error in MOTIONSensor destructor: %s", e)

    @staticmethod
    def decode_camera_status(status: int) -> str:
        """
        Decode the camera status byte into a human-readable string.

        Args:
            status (int): The status byte.

        Returns:
            str: Human-readable status flags.
        """
        flags = []
        if status & (1 << 0): flags.append("READY")
        if status & (1 << 1): flags.append("PROGRAMMED")
        if status & (1 << 2): flags.append("CONFIGURED")
        if status & (1 << 7): flags.append("STREAMING")
        return ", ".join(flags) if flags else "NONE"


