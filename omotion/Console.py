import logging
import struct
import json
import sys
import time
import os
from dataclasses import dataclass
from typing import Optional, Tuple, List

from omotion import MOTIONUart, _log_root
from omotion.config import OW_CMD, OW_CMD_DFU, OW_CMD_ECHO, OW_CMD_HWID, OW_CMD_NOP, OW_CMD_PING, OW_CMD_RESET, OW_CMD_TOGGLE_LED, OW_CMD_VERSION, OW_CONTROLLER, OW_CTRL_BOARDID, OW_CTRL_GET_FAN, OW_CTRL_GET_FSYNC, OW_CTRL_GET_IND, OW_CTRL_GET_LSYNC, OW_CTRL_GET_TEMPS, OW_CTRL_GET_TRIG, OW_CTRL_I2C_RD, OW_CTRL_I2C_SCAN, OW_CTRL_I2C_WR, OW_CTRL_PDUMON, OW_CTRL_READ_ADC, OW_CTRL_READ_GPIO, OW_CTRL_SET_FAN, OW_CTRL_SET_IND, OW_CTRL_SET_TRIG, OW_CTRL_START_TRIG, OW_CTRL_STOP_TRIG, OW_CTRL_TEC_DAC, OW_CTRL_TEC_STATUS, OW_CTRL_TECADC, OW_ERROR

logger = logging.getLogger(f"{_log_root}.Console" if _log_root else "Console")

@dataclass
class PDUMon:
    raws: List[int]     # 16 uint16
    volts: List[float]  # 16 float32

def _parse_pdu_mon(payload: bytes) -> PDUMon:
    if len(payload) != 96:
        raise ValueError(f"Expected 96 bytes, got {len(payload)}")
    # <  = little-endian; 16H = 16 uint16; 16f = 16 float32
    raws_and_volts = struct.unpack("<16H16f", payload)
    raws  = list(raws_and_volts[0:16])
    volts = list(raws_and_volts[16:32])
    return PDUMon(raws=raws, volts=volts)


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
            # The underlying error is already logged by MotionUart.send_packet()
            # Only log here if we want additional context about the I2C operation
            logger.debug(f"I2C read operation failed (underlying error logged by UART layer): {str(e)}")
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
            raise ValueError("Console controller not connected")


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
            raise ValueError("Console controller not connected")

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
        Set the BGR LED state.

        Args:
            rgb_state (int): The desired BGR state (0 = OFF, 1 = IND1, 2 = IND2, 3 = IND3).

        Returns:
            int: The current BGR state after setting.

        Raises:
            ValueError: If the controller is not connected or the BGR state is invalid.
        """
        if not self.uart.is_connected():
            raise ValueError("Console controller not connected")

        if rgb_state not in [0, 1, 2, 3]:
            raise ValueError(
                "Invalid BGR state. Must be 0 (OFF), 1 (IND1), 2 (IND2), or 3 (IND3)"
            )

        try:
            if self.uart.demo_mode:
                return rgb_state

            logger.info("Setting BGR LED state.")

            # Send the BGR state as the reserved byte in the packet
            r = self.uart.send_packet(
                id=None,
                reserved=rgb_state & 0xFF,  # Send the BGR state as a single byte
                packetType=OW_CONTROLLER,
                command=OW_CTRL_SET_IND,
            )

            self.uart.clear_buffer()

            if r.packet_type == OW_ERROR:
                logger.error("Error setting BGR LED state")
                return -1

            logger.info(f"Set BGR LED state to {rgb_state}")
            return rgb_state

        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def get_rgb_led(self) -> int:
        """
        Get the current BGR LED state.

        Returns:
            int: The current BGR state (0 = OFF, 1 = IND1, 2 = IND2, 3 = IND3).

        Raises:
            ValueError: If the controller is not connected.
        """
        if not self.uart.is_connected():
            raise ValueError("Console controller not connected")

        try:
            if self.uart.demo_mode:
                return 1  # Default to RED in demo mode

            logger.info("Getting current BGR LED state.")

            r = self.uart.send_packet(
                id=None, packetType=OW_CONTROLLER, command=OW_CTRL_GET_IND
            )

            self.uart.clear_buffer()

            if r.packet_type == OW_ERROR:
                logger.error("Error getting BGR LED state")
                return -1

            rgb_state = r.reserved
            logger.info(f"Current BGR LED state is {rgb_state}")
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
                raise ValueError("Console controller not connected")

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
                raise ValueError("Console controller not connected")

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
                raise ValueError("Console controller not connected")

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
                raise ValueError("Console controller not connected")

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
                raise ValueError("Console controller not connected")

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
                raise ValueError("Console controller not connected")

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


    def read_gpio_value(self) -> float:
        """
        Read ADC value

        Returns:
            float: The read value.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while retrieving the pulse count.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("Console controller not connected")

            r = self.uart.send_packet(id=None, packetType=OW_CONTROLLER, command=OW_CTRL_READ_GPIO, data=None)
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

            raise  # Re-raise the exception for the caller to handle

    def read_adc_value(self) -> float:
        """
        Read ADC value

        Returns:
            float: The read value.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while retrieving the pulse count.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("Console controller not connected")

            r = self.uart.send_packet(id=None, packetType=OW_CONTROLLER, command=OW_CTRL_READ_ADC, data=None)
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

    def get_temperatures(self) -> tuple[float, float, float]:
        """
        Get the current temperatures from the Console device.

        Returns:
            tuple[float, float, float]: (mcu_temp, safety_temp, ta_temp) in °C.

        Raises:
            ValueError: If the UART is not connected or the payload size is unexpected.
        """
        try:
            if self.uart.demo_mode:
                # Demo values: stable 3-tuple
                return (35.0, 45.0, 25.0)

            if not self.uart.is_connected():
                raise ValueError("Console controller not connected")

            # (Optional) self.uart.clear_buffer()  # If you intend to clear BEFORE request
            r = self.uart.send_packet(
                id=None,
                packetType=OW_CONTROLLER,
                command=OW_CTRL_GET_TEMPS
            )
            self.uart.clear_buffer()  # OK if your send_packet fully returns the response

            if r.packet_type == OW_ERROR:
                raise ValueError("Device returned OW_ERROR for temperatures")

            if r.data_len != 12:
                raise ValueError(f"Unexpected temperature payload length: {r.data_len} (expected 12)")

            mcu_temp, safety_temp, ta_temp = struct.unpack('<fff', r.data)
            logger.info("MCU: %.2f °C, Safety: %.2f °C, TA: %.2f °C", mcu_temp, safety_temp, ta_temp)
            return (mcu_temp, safety_temp, ta_temp)

        except Exception:
            # Let caller handle; preserve your existing behavior
            logger.exception("Failed to get temperatures")
            raise

    def tec_voltage(self, voltage: float | None = None) -> float:
        """
        Get/Set TEC Setpoint voltage.

        Returns:

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while retrieving the TEC Enable.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("Motion Console not connected")
        
            if voltage is not None and voltage >= 0 and voltage <= 5.0:
                # Set TEC Voltage
                logger.info("Setting TEC Voltage to %.2f V", voltage)
                data = struct.pack('<f', float(voltage)) # Convert to 2-byte unsigned int
                r = self.uart.send_packet(id=None, packetType=OW_CONTROLLER, command=OW_CTRL_TEC_DAC, reserved=1, data=data)
            elif voltage is None:
                # Get TEC Voltage
                logger.info("Getting TEC Voltage")
                r = self.uart.send_packet(id=None, packetType=OW_CONTROLLER, command=OW_CTRL_TEC_DAC, reserved=0, data=None)
            else:   
                raise ValueError("Invalid voltage value. Must be between 0 and 5.0 V")
            
            self.uart.clear_buffer()
            # r.print_packet()
            if r.packet_type == OW_ERROR:
                logger.error("Error executing tec_voltage command")
                return 0
            elif r.data_len == 4:
                tec_voltage = struct.unpack('<f', r.data)[0]
                logger.info(f"TEC Voltage is {tec_voltage} V")
                return tec_voltage
            
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def tec_adc(self, channel: int) -> float:
        """
        Get TEC ADC voltages.

        Returns:
            float: The TEC ADC voltage for the specified channel.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while retrieving the TEC Enable.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("Motion Console not connected")
            
            if channel not in [0, 1, 2, 3, 4]:
                raise ValueError("Invalid channel. Must be 0, 1, 2, 3 or 4")
        
            # Get TEC Voltage
            logger.info(f'Getting TEC ADC CH{channel} Voltage')
            r = self.uart.send_packet(id=None, packetType=OW_CONTROLLER, command=OW_CTRL_TECADC, reserved=channel, data=None)
        
            self.uart.clear_buffer()
            # r.print_packet()    
            if r.packet_type == OW_ERROR:
                logger.error("Error executing tec_adc command")
                return 0
            elif r.data_len == 4:            
                tec_voltage = struct.unpack('<f', r.data)[0]
                logger.info(f"CHANNEL {channel}: {tec_voltage} V")
                return tec_voltage
            elif r.data_len == 16:            
                ch0, ch1, ch2, ch3 = struct.unpack('<4f', r.data)
                vals = [ch0, ch1, ch2, ch3]
                logger.info(f"CHANNELS 0-3: {vals} V")
                return vals 
            else:
                logger.error("Unexpected data length for TEC ADC voltage")
                raise ValueError("Unexpected data length for TEC ADC voltage")
            
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def tec_status(self) -> Tuple[float, float, float, float, bool]:
        """
        Get TEC status: (voltage, Temperature Setpoint, TEC Current, TEC Voltage, TEC Good)

        Returns:
            tuple: (volt, temp_set, tec_curr, tec_volt, tec_good)

        Raises:
            ValueError: If not connected or response lengths are unexpected.
            Exception:  If the device reports an OW_ERROR.
        """
        try:
            # Demo mode mock
            if getattr(self.uart, "demo_mode", False):
                return (1.0, 0.5, 0.5, 25.0, True)

            if not self.uart.is_connected():
                raise ValueError("Motion Console not connected")

            logger.debug("Getting TEC Status")

            # 1) Enabled flag
            r = self.uart.send_packet(
                id=None,
                packetType=OW_CONTROLLER,
                command=OW_CTRL_TEC_STATUS,
                data=None
            )
            if r.packet_type == OW_ERROR:
                logger.error("Device returned OW_ERROR for OW_CTRL_TEC_STATUS")
                raise Exception("Error executing tec_status command")
            if r.data_len != 1:
                raise ValueError(f"Unexpected data length for TEC status flag: {r.data_len}, expected 1")
            tec_good = bool(r.data[0])

            # 2) Read all four ADC channels (reserved=4 => all)
            s = self.uart.send_packet(
                id=None,
                packetType=OW_CONTROLLER,
                command=OW_CTRL_TECADC,
                reserved=4,
                data=None
            )
            if s.packet_type == OW_ERROR:
                logger.error("Device returned OW_ERROR for OW_CTRL_TECADC(all)")
                raise Exception("Error executing tec_adc(all) command")
            if s.data_len != 16:
                raise ValueError(f"Unexpected data length for TEC ADC (all): {s.data_len}, expected 16")

            vout, temp_set, tec_curr, tec_volt = struct.unpack('<4f', s.data)

            logger.debug(
                "TEC Status - V: %.6f V, SET: %.6f V, TEC_C: %.6f V, TEC_V: %.6f V, GOOD: %s",
                vout, temp_set, tec_curr, tec_volt, tec_good
            )
            return (f"{vout:.6f}", f"{temp_set:.6f}", f"{tec_curr:.6f}", f"{tec_volt:.6f}", tec_good)
        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def read_board_id(self) -> int:
        """
        Read Board ID

        Returns:
            int: The read value.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while retrieving Board ID.
        """
        try:
            if self.uart.demo_mode:
                return True

            if not self.uart.is_connected():
                raise ValueError("Console controller not connected")

            r = self.uart.send_packet(id=None, packetType=OW_CONTROLLER, command=OW_CTRL_BOARDID, data=None)
            self.uart.clear_buffer()
            # r.print_packet()
            if r.packet_type == OW_ERROR:
                logger.error("Error retrieving Board ID")
                return 0
            if r.data_len == 1:
                # Assuming the pulse count is returned as a 4-byte integer
                boardID = r.data[0]
                return boardID
            else:
                logger.error("Unexpected data length for Board ID")
                return 0
            
        except ValueError as v:
            logger.error("ValueError: %s", v)
            raise  # Re-raise the exception for the caller to handle

        except Exception as e:
            logger.error("Unexpected error during process: %s", e)
            raise  # Re-raise the exception for the caller to handle

    def read_pdu_mon(self) -> Optional[PDUMon]:
        """
        Read PDU MON

        Returns:
            int: 16 raw values read from ADC.
            float: 16 voltage values converted.

        Raises:
            ValueError: If the UART is not connected.
            Exception: If an error occurs while retrieving PDU MON data.
        """
        try:
            if self.uart.demo_mode:
                # Return a fake structure in demo mode
                return PDUMon(
                    raws=[0]*16,
                    volts=[0.0]*16
                )

            if not self.uart.is_connected():
                raise ValueError("Console controller not connected")

            r = self.uart.send_packet(id=None, packetType=OW_CONTROLLER, command=OW_CTRL_PDUMON, data=None)
            self.uart.clear_buffer()
            r.print_packet()
            if r.packet_type == OW_ERROR:
                logger.error("Error retrieving PDU MON data")
                return None

            if r.data_len != 96 or r.data is None:
                logger.error("Unexpected data length for PDU MON data: %s", r.data_len)
                return None

            # r.data should be a bytes-like object
            pdu = _parse_pdu_mon(bytes(r.data[:96]))
            logger.info("PDU MON: raws=%s", pdu.raws)
            logger.info("PDU MON: volts=%s", ["%.3f" % v for v in pdu.volts])
            return pdu

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
        
