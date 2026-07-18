"""Unit tests for parse_camera_telemetry (sensor-fw#94 / #162).

Builds synthetic cam_telemetry_response_t blobs byte-for-byte against the wire
format in sensor-fw Core/Inc/camera_telemetry.h and checks the parser's
engineering-unit conversions and rejection paths. No hardware.
"""
import struct

from omotion.MotionSensor import (
    CAM_TELEMETRY_VERSION,
    _CAM_TELEM_CAM_FMT,
    _CAM_TELEM_CAM_SIZE,
    _CAM_TELEM_SIZE,
    parse_camera_telemetry,
)

NOMINAL = dict(
    updated_ms=123456, frame_counter=100, dgain_raw=0x010000,
    avdd=1911, dovdd=1229, dvdd=819,          # 2.799 / 1.800 / 1.200 V
    tpm_avg=0x2D80, tpm0=0x2D00, tpm1=0x2E00,  # 45.5 / 45.0 / 46.0 C
    tc_row=0x0123, expo_cmd=0x0048, expo_applied=0x0048,
    again_raw=0x1000,                          # 0x3508=0x10 -> 16x
    isp_real=0x0100, isp_dig=0x0400, isp_blc=0x0080, isp_expo=0x0048,
    blc=[0x0100] * 8,
    tpm_status=0, vm_live=0, vm_cp=0, vm_latched=0, vm_cp_latched=0,
    wd=[0, 0, 0, 0x2D, 0x80, 0], sc_state=0x07, otp=[0xAA, 0x55],
    trig=0, yavg=42, aec=0xA8, dcg=0x40, blc_ctrl=0x23, isp_ctrl=0x34,
    err=0, sweeps=5,
)


def make_cam(**overrides):
    v = dict(NOMINAL)
    v.update(overrides)
    return struct.pack(
        _CAM_TELEM_CAM_FMT,
        v["updated_ms"], v["frame_counter"], v["dgain_raw"],
        v["avdd"], v["dovdd"], v["dvdd"],
        v["tpm_avg"], v["tpm0"], v["tpm1"],
        v["tc_row"], v["expo_cmd"], v["expo_applied"], v["again_raw"],
        v["isp_real"], v["isp_dig"], v["isp_blc"], v["isp_expo"],
        *v["blc"],
        v["tpm_status"], v["vm_live"], v["vm_cp"], v["vm_latched"],
        v["vm_cp_latched"], *v["wd"], v["sc_state"], *v["otp"],
        v["trig"], v["yavg"], v["aec"], v["dcg"], v["blc_ctrl"],
        v["isp_ctrl"], v["err"], v["sweeps"],
    )


def make_blob(cams=None, version=CAM_TELEMETRY_VERSION, valid=0xFF, size=None):
    cams = cams if cams is not None else [make_cam() for _ in range(8)]
    size = _CAM_TELEM_CAM_SIZE if size is None else size
    return struct.pack("<BBBB", version, valid, size, 0) + b"".join(cams)


def test_wire_sizes_match_firmware():
    assert _CAM_TELEM_CAM_SIZE == 78
    assert _CAM_TELEM_SIZE == 628


def test_nominal_conversions():
    t = parse_camera_telemetry(make_blob())
    assert t is not None
    assert t["version"] == 1 and t["valid_mask"] == 0xFF
    c = t["cameras"][0]
    assert c["valid"] is True
    assert abs(c["avdd_v"] - 2.7993) < 0.001
    assert abs(c["dovdd_v"] - 1.8003) < 0.001
    assert abs(c["dvdd_v"] - 1.1997) < 0.001
    assert c["tpm_avg_c"] == 45.5
    assert c["tpm0_c"] == 45.0 and c["tpm1_c"] == 46.0
    assert c["again_x"] == 16.0          # code 0x100/16
    assert c["dgain_x"] == 1.0           # 0x350A=0x01 -> code 1024/1024
    assert c["expo_cmd"] == 0x48 and c["expo_applied"] == 0x48
    assert c["sc_state"] == 0x7
    assert c["otp_crc"] == (0xAA, 0x55)
    assert c["blc_ctrl"] == 0x23 and c["isp_ctrl"] == 0x34
    assert c["blc_offsets"] == [0x0100] * 8
    assert c["sweep_count"] == 5 and c["i2c_err_count"] == 0
    assert c["frame_counter"] == 100 and c["updated_ms"] == 123456


def test_negative_temperature_rule():
    # DS 10.5.23: 0xD000 -> -(0xD000-0xC000)/256 = -16.0 C
    t = parse_camera_telemetry(make_blob([make_cam(tpm_avg=0xD000)] + [make_cam()] * 7))
    assert t["cameras"][0]["tpm_avg_c"] == -16.0
    # Boundary: 0xC000 itself is positive (192.0 C) per the "> 0xC000" rule
    t = parse_camera_telemetry(make_blob([make_cam(tpm_avg=0xC000)] + [make_cam()] * 7))
    assert t["cameras"][0]["tpm_avg_c"] == 192.0


def test_analog_gain_code_assembly():
    # 0x3508=0x01, 0x3509=0x80 -> code[8:4]=1, code[3:0]=8 -> 0x18/16 = 1.5x
    t = parse_camera_telemetry(make_blob([make_cam(again_raw=0x0180)] + [make_cam()] * 7))
    assert t["cameras"][0]["again_x"] == 1.5


def test_digital_gain_bit_packing():
    # 0x350A=0x02, 0x350B=0x00, 0x350C=0x00 -> code 2<<10 = 2048 -> 2.0x
    t = parse_camera_telemetry(make_blob([make_cam(dgain_raw=0x020000)] + [make_cam()] * 7))
    assert t["cameras"][0]["dgain_x"] == 2.0
    # LSB bits: 0x350C=0xC0 contributes code[1:0]=3
    t = parse_camera_telemetry(make_blob([make_cam(dgain_raw=0x0100C0)] + [make_cam()] * 7))
    assert t["cameras"][0]["dgain_x"] == (1024 + 3) / 1024.0


def test_vm_raw_masking_and_blc_msb_mask():
    # Rail codes are 12-bit; reserved high nibble must be ignored.
    t = parse_camera_telemetry(make_blob([make_cam(avdd=0xF777)] + [make_cam()] * 7))
    assert abs(t["cameras"][0]["avdd_v"] - (0x777 * 6.0 / 4096.0)) < 1e-9
    # BLC applied offsets are 15-bit ({MSB[6:0],LSB}).
    t = parse_camera_telemetry(make_blob([make_cam(blc=[0x8123] * 8)] + [make_cam()] * 7))
    assert t["cameras"][0]["blc_offsets"][0] == 0x0123


def test_valid_mask_bits():
    t = parse_camera_telemetry(make_blob(valid=0b00000101))
    valids = [c["valid"] for c in t["cameras"]]
    assert valids == [True, False, True] + [False] * 5


def test_rejects_malformed():
    assert parse_camera_telemetry(None) is None
    assert parse_camera_telemetry(b"") is None
    assert parse_camera_telemetry(make_blob()[:-1]) is None          # short
    assert parse_camera_telemetry(make_blob(version=2)) is None      # future version
    assert parse_camera_telemetry(make_blob(size=77)) is None        # struct drift
