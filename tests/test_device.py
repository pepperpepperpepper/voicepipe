from __future__ import annotations

from voicepipe.device import parse_device_index


def test_parse_device_index_accepts_none() -> None:
    assert parse_device_index(None) == (None, None)


def test_parse_device_index_accepts_empty_string() -> None:
    assert parse_device_index("") == (None, None)


def test_parse_device_index_accepts_int() -> None:
    assert parse_device_index(12) == (12, None)


def test_parse_device_index_accepts_int_string() -> None:
    assert parse_device_index(" 12 ") == (12, None)


def test_parse_device_index_rejects_bool() -> None:
    assert parse_device_index(True) == (None, "device must be a device index or name")


def test_parse_device_index_rejects_non_int_string() -> None:
    idx, err = parse_device_index("nonexistent-device-xyz123")
    assert idx is None
    assert err == "device not found: nonexistent-device-xyz123"
