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
    assert parse_device_index(True) == (None, "device must be an integer")


def test_parse_device_index_rejects_non_int_string() -> None:
    assert parse_device_index("abc") == (None, "device must be an integer")
