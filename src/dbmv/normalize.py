"""Canonical value representation, so the same data hashes the same on any engine.

Engines disagree on trivia: Oracle returns DATE as datetime, numerics come back as
Decimal('1') on one side and Decimal('1.00') on the other, CHAR columns are padded,
floats differ in the last bit. Each value is turned into a stable string first.
"""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal

NULL = "\x00NULL"


def canonical(value, float_digits: int = 12, rstrip_strings: bool = True) -> str:
    if value is None:
        return NULL
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if value.is_nan():
            return "NaN"
        if value == value.to_integral_value():
            return str(int(value))
        return format(value.normalize(), "f")
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if value.is_integer():
            return str(int(value))
        return format(Decimal(repr(value)).quantize(Decimal(10) ** -float_digits).normalize(), "f")
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(dt.UTC).replace(tzinfo=None)
        if value.time() == dt.time(0):
            return value.date().isoformat()
        return value.isoformat(timespec="microseconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    text = str(value)
    return text.rstrip() if rstrip_strings else text
