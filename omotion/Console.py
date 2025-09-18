import logging
import struct
import json
import sys
import time
import os
from typing import Optional

from omotion import MOTIONUart
from omotion.config import OW_CMD, OW_CMD_DFU, OW_CMD_ECHO, OW_CMD_HWID, OW_CMD_NOP, OW_CMD_PING, OW_CMD_RESET, OW_CMD_TOGGLE_LED, OW_CMD_VERSION, OW_CONTROLLER, OW_CTRL_GET_FAN, OW_CTRL_GET_FSYNC, OW_CTRL_GET_IND, OW_CTRL_GET_LSYNC, OW_CTRL_GET_TRIG, OW_CTRL_I2C_RD, OW_CTRL_I2C_SCAN, OW_CTRL_I2C_WR, OW_CTRL_SET_FAN, OW_CTRL_SET_IND, OW_CTRL_SET_TRIG, OW_CTRL_START_TRIG, OW_CTRL_STOP_TRIG, OW_CTRL_TEC_STATUS, OW_ERROR

logger = logging.getLogger("Console")


class MOTIONConsole:
    def __init__(self, uart: MOTIONUart):
        """
        Initialize the MOTIONConsole Module.            
        """

        self.uart = uart

        if self.uart and not self.uart.asyncMode:
            self.uart.check_usb_status()
            if self.uart.is_connected():
                logger.info("MOTION MOTIONConsole connected.")
            else:
                logger.info("MOTION MOTIONConsole NOT Connected.")    

    def is_connected(self)-> bool:        
        """
        Check if the MOTIONConsole is connected.   
        Returns True if connected, False otherwise.
        """
        if self.uart and self.uart.is_connected():
            return True
        else:
            return False
        
    def ping(self) -> bool:        
        """    
        Send a ping command to the MOTIONConsole and receive a response.
        Returns the response from the MOTIONConsole.
        """ 
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("Console Device not connected")

            logger.info("Send Ping to Device.")
            r = self.uart.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_PING)
            self.uart.clear_buffer()
            logger.info("Received Ping from Device.")
            # r.print_packet()

            if r.packet_type == OW_ERROR:
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
        Retrieve the firmware version of the Console Module.

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
                logger.error("Console Module not connected")
                return 'v0.0.0'

            r = self.uart.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_VERSION)
            self.uart.clear_buffer()
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
                logger.error("Console Module not connected")
                return None, None

            # Check if echo_data is a byte array
            if echo_data is not None and not isinstance(echo_data, (bytes, bytearray)):
                raise TypeError("echo_data must be a byte array")

            r = self.uart.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_ECHO, data=echo_data)
            self.uart.clear_buffer()
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
        Toggle the LED on the Console Module.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while toggling the LED.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                logger.error("Console Module not connected")
                return False

            r = self.uart.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_TOGGLE_LED)
            self.uart.clear_buffer()
            # r.print_packet()
            return True

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def get_hardware_id(self) -> str:
        """
        Retrieve the hardware ID of the Console Module.

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
                logger.error("Console Module not connected")
                return None

            r = self.uart.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_HWID)
            self.uart.clear_buffer()
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


    def enter_dfu(self) -> bool:
        """
        Perform a soft reset to enter DFU mode on Console device.

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
                raise ValueError("Console Device not connected")

            r = self.uart.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_DFU)
            self.uart.clear_buffer()
            # r.print_packet()
            if r.packet_type == OW_ERROR:
                logger.error("Error setting DFU mode for device")
                return False
            else:
                return True
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def soft_reset(self) -> bool:
        """
        Perform a soft reset on the Console device.

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
                raise ValueError("Console Module not connected")

            r = self.uart.send_packet(id=None, packetType=OW_CMD, command=OW_CMD_RESET)
            self.uart.clear_buffer()
            # r.print_packet()
            if r.packet_type == OW_ERROR:
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

    def scan_i2c_mux_channel(self, mux_index: int, channel: int) -> list[int]:
        """
        Scan a specific channel on an I2C MUX and return detected I2C addresses.

        Args:
            mux_index (int): Index of the I2C MUX (e.g., 0 for MUX at 0x70, 1 for 0x71).
            channel (int): Channel number on the MUX to activate (0-7).

        Returns:
            list[int]: List of detected I2C device addresses on the specified mux/channel.
                    Returns an empty list if no devices are found.

        Raises:
            ValueError: If the mux index or channel is out of range.
            Exception: For unexpected UART communication issues.
        """
        if channel < 0 or channel > 7:
            raise ValueError(f"Invalid channel {channel}, must be 0-7")
        if mux_index not in [0, 1]:
            raise ValueError(f"Invalid mux index {mux_index}, must be 0 or 1")

        try:
            # Send I2C scan command with mux index and channel as payload
            r = self.uart.send_packet(
                id=None,
                packetType=OW_CONTROLLER,
                command=OW_CTRL_I2C_SCAN,
                data=bytes([mux_index, channel])
            )
            self.uart.clear_buffer()

            if r.packet_type == OW_ERROR:
                logger.error("Error scanning I2C mux %d channel %d", mux_index, channel)
                return []

            # Return list of detected I2C addresses
            return list(r.data) if r.data else []

        except Exception as e:
            logger.error("Exception while scanning I2C mux %d channel %d: %s", mux_index, channel, e)
            raise


    def read_i2c_packet(self, mux_index: int, channel: int, device_addr: int, reg_addr: int, read_len: int) -> tuple[bytes, int]:
        """
        Read data from I2C device through MUX
        
        Args:
            mux_index: Which MUX to use (0 or 1)
            channel: Which MUX channel to select (0-7)
            device_addr: I2C device address (7-bit)
            reg_addr: Register address to read from
            read_len: Number of bytes to read
            
        Returns:
            tuple[bytes, int]: The received data and its length, None if failed
        """
        """Validate MUX index and channel parameters"""
        if mux_index not in (0, 1):
            raise ValueError(f"Invalid mux_index {mux_index}. Must be 0 or 1")
        if channel < 0 or channel > 7:
            raise ValueError(f"Invalid channel {channel}. Must be 0-7")

        try:
            # Build packet: [CMD, MUX_IDX, CHANNEL, DEV_ADDR, REG_ADDR, READ_LEN]
            packet = struct.pack(
                'BBBBB',
                mux_index,
                channel,
                device_addr,
                reg_addr,
                read_len
            )
            
            r = self.uart.send_packet(
                id=None,
                packetType=OW_CONTROLLER,
                command=OW_CTRL_I2C_RD,
                data=packet,
            )

            self.uart.clear_buffer()
            # r.print_packet()

            if r.packet_type == OW_ERROR:
                logger.error("Error Reading I2C Device")
                return None, None

            if r.data_len > 0:
                return r.data, r.data_len
            else:
                return None, None
            
        except Exception as e:
            print(f"I2C Read failed: {str(e)}")
            return None, None

    def write_i2c_packet(self, mux_index: int, channel: int, device_addr: int, reg_addr: int, data: bytes) -> bool:
        """
        Write data to I2C device through MUX
        
        Args:
            mux_index: Which MUX to use (0 or 1)
            channel: Which MUX channel to select (0-7)
            device_addr: I2C device address (7-bit)
            reg_addr: Register address to write to
            data: Bytes to write
            
        Returns:
            bool: True if write succeeded, False otherwise
        """
        """Validate MUX index and channel parameters"""
        if mux_index not in (0, 1):
            raise ValueError(f"Invalid mux_index {mux_index}. Must be 0 or 1")
        if channel < 0 or channel > 7:
            raise ValueError(f"Invalid channel {channel}. Must be 0-7")

        try:            
            # Build packet: [CMD, MUX_IDX, CHANNEL, DEV_ADDR, REG_ADDR] + data
            header = struct.pack(
                'BBBBB',
                mux_index,
                channel,
                device_addr,
                reg_addr,
                len(data)
            )
            packet = header + data
            
            r = self.uart.send_packet(
                id=None,
                packetType=OW_CONTROLLER,
                command=OW_CTRL_I2C_WR,
                data=packet,
            )

            self.uart.clear_buffer()
            # r.print_packet()

            if r.packet_type == OW_ERROR:
                logger.error("Error Writing I2C Device")
                return False
            else:
                return True
            
        except Exception as e:
            print(f"I2C Write failed: {str(e)}")
            return False

    def set_fan_speed(self, fan_speed: int = 50) -> int:
        """
        Get the current output fan percentage.

        Args:
            fan_speed (int): The desired fan speed (default is 50).

        Returns:
            int: The current output fan percentage.

        Raises:
            ValueError: If the controller is not connected.
        """
        if not self.uart.is_connected():
            raise ValueError("High voltage controller not connected")


        if fan_speed not in range(101):
            raise ValueError("Invalid fan speed. Must be 0 to 100")

        try:
            if self.uart.demo_mode:
                return 40

            logger.info("Getting current output voltage.")

            data = bytes(
                [
                    fan_speed & 0xFF,  # Low byte (least significant bits)
                ]
            )

            r = self.uart.send_packet(
                id=None,
                packetType=OW_CONTROLLER,
                command=OW_CTRL_SET_FAN,
                data=data,
            )

            self.uart.clear_buffer()
            # r.print_packet()

            if r.packet_type == OW_ERROR:
                logger.error("Error setting Fan Speed")
                return -1

            logger.info(f"Set fan speed to {fan_speed}")
            return fan_speed

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def get_fan_speed(self) -> int:
        """
        Get the current output fan percentage.

        Returns:
            int: The current output fan percentage.

        Raises:
            ValueError: If the controller is not connected.
        """
        if not self.uart.is_connected():
            raise ValueError("High voltage controller not connected")

        try:
            if self.uart.demo_mode:
                return 40.0

            logger.info("Getting current output voltage.")

            r = self.uart.send_packet(
                id=None, packetType=OW_CONTROLLER, command=OW_CTRL_GET_FAN
            )

            self.uart.clear_buffer()
            # r.print_packet()

            if r.packet_type == OW_ERROR:
                logger.error("Error setting HV")
                return 0.0

            elif r.data_len == 1:
                fan_value = r.data[0]
                logger.info(f"Output fan speed is {fan_value}")
                return fan_value
            else:
                logger.error("Error getting output voltage from device")
                return -1

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def set_rgb_led(self, rgb_state: int) -> int:
        """
        Set the RGB LED state.

        Args:
            rgb_state (int): The desired RGB state (0 = OFF, 1 = IND1, 2 = IND2, 3 = IND3).

        Returns:
            int: The current RGB state after setting.

        Raises:
            ValueError: If the controller is not connected or the RGB state is invalid.
        """
        if not self.uart.is_connected():
            raise ValueError("High voltage controller not connected")

        if rgb_state not in [0, 1, 2, 3]:
            raise ValueError(
                "Invalid RGB state. Must be 0 (OFF), 1 (IND1), 2 (IND2), or 3 (IND3)"
            )

        try:
            if self.uart.demo_mode:
                return rgb_state

            logger.info("Setting RGB LED state.")

            # Send the RGB state as the reserved byte in the packet
            r = self.uart.send_packet(
                id=None,
                reserved=rgb_state & 0xFF,  # Send the RGB state as a single byte
                packetType=OW_CONTROLLER,
                command=OW_CTRL_SET_IND,
            )

            self.uart.clear_buffer()

            if r.packet_type == OW_ERROR:
                logger.error("Error setting RGB LED state")
                return -1

            logger.info(f"Set RGB LED state to {rgb_state}")
            return rgb_state

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def get_rgb_led(self) -> int:
        """
        Get the current RGB LED state.

        Returns:
            int: The current RGB state (0 = OFF, 1 = IND1, 2 = IND2, 3 = IND3).

        Raises:
            ValueError: If the controller is not connected.
        """
        if not self.uart.is_connected():
            raise ValueError("High voltage controller not connected")

        try:
            if self.uart.demo_mode:
                return 1  # Default to RED in demo mode

            logger.info("Getting current RGB LED state.")

            r = self.uart.send_packet(
                id=None, packetType=OW_CONTROLLER, command=OW_CTRL_GET_IND
            )

            self.uart.clear_buffer()

            if r.packet_type == OW_ERROR:
                logger.error("Error getting RGB LED state")
                return -1

            rgb_state = r.reserved
            logger.info(f"Current RGB LED state is {rgb_state}")
            return rgb_state

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle


    def set_trigger_json(self, data=None) -> dict:
        """
        Set the trigger configuration for console device.

        Args:
            data (dict): A dictionary containing the trigger configuration.

        Returns:
            dict: JSON response from the device.

        Raises:
            ValueError: If `data` is None or the UART is not connected.
            Exception: If an error occurs while setting the trigger.
        """
        try:
            if self.uart.demo_mode:
                return None

            # Ensure data is not None and is a valid dictionary
            if data is None:
                logger.error("Data cannot be None.")
                return None

            if not self.uart.is_connected():
                raise ValueError("High voltage controller not connected")

            try:
                json_string = json.dumps(data)
            except json.JSONDecodeError as e:
                logger.error(f"Data must be valid JSON: {e}")
                return None

            payload = json_string.encode('utf-8')

            r = self.uart.send_packet(id=None, packetType=OW_CONTROLLER, command=OW_CTRL_SET_TRIG, data=payload)
            self.uart.clear_buffer()

            if r.packet_type != OW_ERROR and r.data_len > 0:
                # Parse response as JSON, if possible
                try:
                    response_json = json.loads(r.data.decode('utf-8'))
                    return response_json
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding JSON: {e}")
                    return None
            else:
                return None
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def get_trigger_json(self) -> dict:
        """
        Start the trigger on the Console device.

        Returns:
            bool: True if the trigger was started successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while starting the trigger.
        """
        try:
            if self.uart.demo_mode:
                return None

            if not self.uart.is_connected():
                raise ValueError("High voltage controller not connected")

            r = self.uart.send_packet(id=None, packetType=OW_CONTROLLER, command=OW_CTRL_GET_TRIG, data=None)
            self.uart.clear_buffer()
            data_object = None
            try:
                data_object = json.loads(r.data.decode('utf-8'))
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding JSON: {e}")
            return data_object
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def start_trigger(self) -> bool:
        """
        Start the trigger on the Console device.

        Returns:
            bool: True if the trigger was started successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while starting the trigger.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("High voltage controller not connected")

            r = self.uart.send_packet(id=None, packetType=OW_CONTROLLER, command=OW_CTRL_START_TRIG, data=None)
            self.uart.clear_buffer()
            # r.print_packet()
            if r.packet_type == OW_ERROR:
                logger.error("Error starting trigger")
                return False
            else:
                return True
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def stop_trigger(self) -> bool:
        """
        Stop the trigger on the Console device.

        Returns:
            bool: True if the trigger was started successfully, False otherwise.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while starting the trigger.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("High voltage controller not connected")

            r = self.uart.send_packet(id=None, packetType=OW_CONTROLLER, command=OW_CTRL_STOP_TRIG, data=None)
            self.uart.clear_buffer()
            # r.print_packet()
            if r.packet_type == OW_ERROR:
                logger.error("Error stopping trigger")
                return False
            else:
                return True
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def get_fsync_pulsecount(self) -> int:
        """
        Get FSYNC pulse count from the Console device.

        Returns:
            int: The number of FSYNC pulses received.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while retrieving the pulse count.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("High voltage controller not connected")

            r = self.uart.send_packet(id=None, packetType=OW_CONTROLLER, command=OW_CTRL_GET_FSYNC, data=None)
            self.uart.clear_buffer()
            # r.print_packet()
            if r.packet_type == OW_ERROR:
                logger.error("Error retrieving FSYNC pulse count")
                return 0
            
            if r.data_len == 4:
                # Assuming the pulse count is returned as a 4-byte integer
                pulse_count = struct.unpack('<I', r.data)[0]
                return pulse_count
            else:
                logger.error("Unexpected data length for FSYNC pulse count")
                return 0
            
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def get_lsync_pulsecount(self) -> int:
        """
        Get LSYNC pulse count from the Console device.

        Returns:
            int: The number of LSYNC pulses received.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while retrieving the pulse count.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("High voltage controller not connected")

            r = self.uart.send_packet(id=None, packetType=OW_CONTROLLER, command=OW_CTRL_GET_LSYNC, data=None)
            self.uart.clear_buffer()
            # r.print_packet()
            if r.packet_type == OW_ERROR:
                logger.error("Error retrieving LSYNC pulse count")
                return 0
            if r.data_len == 4:
                # Assuming the pulse count is returned as a 4-byte integer
                pulse_count = struct.unpack('<I', r.data)[0]
                return pulse_count
            else:
                logger.error("Unexpected data length for LSYNC pulse count")
                return 0
            
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle


    def get_tec_enabled(self) -> bool:
        """
        Get TEC Enabled from the Console device.

        Returns:
            bool: returns true if TEC DAC initialized properly.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while retrieving the TEC Enable.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("High voltage controller not connected")

            r = self.uart.send_packet(id=None, packetType=OW_CONTROLLER, command=OW_CTRL_TEC_STATUS, data=None)
            self.uart.clear_buffer()
            # r.print_packet()
            if r.packet_type == OW_ERROR:
                logger.error("Error retrieving TEC Enabled")
                return False
            if r.data_len == 1:
                # Assuming the TEC enabled is returned as a boolean
                return bool(r.data[0])
            else:
                logger.error("Unexpected data length for TEC enabled")
                return False
            
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def disconnect(self):
        """
        Disconnect the UART and clean up.
        """
        if self.uart:
            logger.info("Disconnecting MOTIONConsole UART...")
            self.uart.disconnect()  
            self.uart = None

    def __del__(self):
        """
        Fallback cleanup when the object is garbage collected.
        """
        try:
            self.disconnect()
        except Exception as e:
            logger.warning("Error in MOTIONConsole destructor: %s", e)
        
