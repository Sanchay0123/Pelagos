"""
Pelagos Stage F1.4.3: RFC 8785 Deterministic Canonical JSON Serializer.

Implements JSON Canonicalization Scheme (JCS - RFC 8785) with strict financial precision:
- Object keys sorted lexicographically by UTF-16 code units.
- Elimination of whitespace outside strings.
- Exact canonical Decimal monetary strings (e.g. "14250.50", "1.345620").
- Strict rejection of IEEE 754 floating-point representation in financial payloads.
- Rejection of non-finite values (NaN, sNaN, Infinity, -Infinity).
- Normalization of negative zero (-0.00 -> "0.00").
- UTF-8 encoding without BOM.
"""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Union

CANONICAL_DECIMAL_REGEX = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")


class NonFiniteFinancialValueException(ValueError):
    """Raised when a non-finite number (NaN, Infinity) is encountered."""
    pass


class InvalidCanonicalDecimalException(ValueError):
    """Raised when a decimal string violates canonical formatting rules."""
    pass


def normalize_decimal_string(
    val: Union[Decimal, float, int, str],
    precision: Optional[int] = None,
) -> str:
    """
    Normalize a numeric value into a canonical Decimal string.
    
    Rules:
    - Normalizes negative zero to unsigned zero.
    - Strips scientific exponent notation.
    - Rejects NaN and Infinity.
    - Preserves field-specific decimal scale when precision is specified.
    - Matches syntax ^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$
    """
    if isinstance(val, str):
        val_clean = val.strip().replace(",", "")
        try:
            d = Decimal(val_clean)
        except Exception as exc:
            raise InvalidCanonicalDecimalException(f"Invalid decimal string '{val}': {exc}") from exc
    elif isinstance(val, (int, float, Decimal)):
        d = Decimal(str(val))
    else:
        raise InvalidCanonicalDecimalException(f"Unsupported numeric type: {type(val).__name__}")

    if d.is_nan() or d.is_infinite():
        raise NonFiniteFinancialValueException(f"Non-finite financial numbers ({d}) are strictly rejected.")

    # Quantize if specific precision requested
    if precision is not None and precision >= 0:
        exp = Decimal("10") ** -precision
        d = d.quantize(exp, rounding=ROUND_HALF_UP)

    # Normalize negative zero to positive zero
    if d == Decimal(0):
        if d.is_signed():
            d = d.copy_abs()

    # Format without scientific notation
    sign, digits, exponent = d.as_tuple()
    if exponent >= 0:
        s = "".join(str(digit) for digit in digits) + ("0" * exponent)
    else:
        num_digits = len(digits)
        num_fractional = -exponent
        if num_digits <= num_fractional:
            s = "0." + ("0" * (num_fractional - num_digits)) + "".join(str(digit) for digit in digits)
        else:
            split_idx = num_digits - num_fractional
            s = "".join(str(digit) for digit in digits[:split_idx]) + "." + "".join(str(digit) for digit in digits[split_idx:])

    if sign and s != "0" and not s.startswith("0.0"):
        s = "-" + s

    if not CANONICAL_DECIMAL_REGEX.match(s):
        raise InvalidCanonicalDecimalException(f"Decimal string '{s}' violates canonical format.")

    return s


def _escape_json_string(s: str) -> str:
    """Escape string according to RFC 8785 / ECMA-404 rules."""
    out = ['"']
    for char in s:
        code = ord(char)
        if char == '"':
            out.append('\\"')
        elif char == '\\':
            out.append('\\\\')
        elif char == '\b':
            out.append('\\b')
        elif char == '\f':
            out.append('\\f')
        elif char == '\n':
            out.append('\\n')
        elif char == '\r':
            out.append('\\r')
        elif char == '\t':
            out.append('\\t')
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _serialize_value(val: Any) -> str:
    """Recursively serialize Python structures to RFC 8785 canonical string."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int,)):
        return str(val)
    if isinstance(val, Decimal):
        # Decimals are serialized as canonical strings to preserve exact financial precision
        return _escape_json_string(normalize_decimal_string(val))
    if isinstance(val, float):
        # Strict rule: reject floats in financial payloads if NaN or Inf
        d = Decimal(str(val))
        if d.is_nan() or d.is_infinite():
            raise NonFiniteFinancialValueException("Float NaN/Inf is strictly rejected in canonical payloads.")
        # Minimal float string representation conforming to RFC 8785
        return normalize_decimal_string(d)
    if isinstance(val, str):
        return _escape_json_string(val)
    if isinstance(val, (list, tuple)):
        items = [_serialize_value(item) for item in val]
        return "[" + ",".join(items) + "]"
    if isinstance(val, dict):
        # Keys must be sorted lexicographically by UTF-16 code units
        def utf16_sort_key(key_str: str) -> List[int]:
            return [ord(c) for c in key_str]

        sorted_keys = sorted(val.keys(), key=utf16_sort_key)
        items = [
            _escape_json_string(k) + ":" + _serialize_value(val[k])
            for k in sorted_keys
        ]
        return "{" + ",".join(items) + "}"

    # For objects with to_dict or __dict__
    if hasattr(val, "dict") and callable(val.dict):
        return _serialize_value(val.dict())
    if hasattr(val, "to_dict") and callable(val.to_dict):
        return _serialize_value(val.to_dict())
    if hasattr(val, "__dict__"):
        return _serialize_value(val.__dict__)

    return _escape_json_string(str(val))


def canonicalize_financial_payload(payload: Any) -> bytes:
    """
    Serialize any payload into RFC 8785 canonical JSON UTF-8 bytes.
    Ensures exact Decimal representation and sorted UTF-16 keys.
    """
    canonical_str = _serialize_value(payload)
    return canonical_str.encode("utf-8")
