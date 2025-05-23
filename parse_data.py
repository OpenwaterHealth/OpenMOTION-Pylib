import struct
import csv
import os

# --- Constants ---
HISTO_SIZE_WORDS = 1024                  # 1024 32-bit integers
PACKET_HEADER_SIZE = 6                  # SOF (1) + type (1) + length (4)
PACKET_FOOTER_SIZE = 3                  # CRC (2) + EOF (1)
HISTO_BLOCK_OVERHEAD = 3                # SOH (1) + cam_id (1) + EOH (1)
HISTO_BLOCK_SIZE = HISTO_BLOCK_OVERHEAD + HISTO_SIZE_WORDS * 4  # Per camera

# --- CRC function (placeholder) ---
def compute_crc16(data: bytes) -> int:
    import binascii
    return binascii.crc_hqx(data, 0xFFFF)

# --- Parse a single packet ---
def parse_histogram_packet(packet: bytes):
    if len(packet) < PACKET_HEADER_SIZE + PACKET_FOOTER_SIZE:
        raise ValueError("Packet too short")

    offset = 0

    # Header
    sof = packet[offset]
    if sof != 0xAA:
        raise ValueError("Invalid SOF")
    offset += 1

    pkt_type = packet[offset]
    if pkt_type != 0x00:
        raise ValueError("Unsupported packet type")
    offset += 1

    packet_length = struct.unpack_from("<I", packet, offset)[0]
    offset += 4

    start_of_payload = offset
    end_of_payload = packet_length - PACKET_FOOTER_SIZE #start_of_payload + payload_length

    if end_of_payload + PACKET_FOOTER_SIZE > len(packet):
        raise ValueError("Incomplete packet payload")

    histograms = {}
    packet_ids = {}

    while offset < end_of_payload:
        if packet[offset] != 0xFF:
            raise ValueError(f"Missing SOH at offset {offset}")
        offset += 1

        cam_id = packet[offset]
        offset += 1

        histo_data = packet[offset : offset + HISTO_SIZE_WORDS * 4]
        if len(histo_data) < HISTO_SIZE_WORDS * 4:
            raise ValueError("Histogram data too short")

        histogram = list(struct.unpack_from(f"<{HISTO_SIZE_WORDS}I", histo_data))
        offset += HISTO_SIZE_WORDS * 4

        if packet[offset] != 0xEE:
            raise ValueError("Missing EOH")
        offset += 1

        # Extract and mask packet ID from last word
        last_word = histogram[-1]
        packet_id = (last_word >> 24) & 0xFF
        histogram[-1] = last_word & 0x00FFFFFF

        histograms[cam_id] = histogram
        packet_ids[cam_id] = packet_id
        print(packet_id)

    # Footer
    crc_expected = struct.unpack_from("<H", packet, offset)[0]
    offset += 2

    if packet[offset] != 0xDD:
        raise ValueError("Missing EOF")

    # CRC check
    crc_computed = compute_crc16(packet[1 : offset - 2])  # from 'type' to EOH
    # if crc_computed != crc_expected:
    #     raise ValueError(f"CRC mismatch: expected {crc_expected:04X}, got {crc_computed:04X}")

    return histograms, packet_ids, offset + 1  # return dict + total packet size consumed

# --- Process .bin file and convert to CSVs ---
def process_bin_file(filename, output_csv):
    import csv
    import os

    with open(filename, "rb") as f:
        data = f.read()

    offset = 0
    packet_count = 0

    with open(output_csv, "w", newline='') as csvfile:
        writer = csv.writer(csvfile)
        header = ["cam_id", "frame_id"] + [str(i) for i in range(HISTO_SIZE_WORDS)] + ["sum"]
        writer.writerow(header)

        while offset + PACKET_HEADER_SIZE + PACKET_FOOTER_SIZE < len(data):
            try:
                histograms, packet_ids, consumed = parse_histogram_packet(data[offset:])
                offset += consumed
                packet_count += 1

                for cam_id, histo in histograms.items():
                    frame_id = packet_ids.get(cam_id, 0)
                    row_sum = sum(histo)
                    row = [cam_id, frame_id] + histo + [row_sum]
                    writer.writerow(row)

            except Exception as e:
                print(f"Packet parse error at offset {offset}: {e}")
                break

    print(f"✅ Done: Parsed {packet_count} packets and wrote data to '{output_csv}'")

# --- Example usage ---
if __name__ == "__main__":
    process_bin_file("my_file.txt", "output.csv")
