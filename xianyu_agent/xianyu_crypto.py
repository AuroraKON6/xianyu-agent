"""Xianyu crypto utilities.

Provides message encryption/decryption and ID generation for the
Xianyu IM WebSocket protocol. Ported from XianyuAutoAgent.
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import struct
import time
from typing import Any, Dict, List


def trans_cookies(cookies_str: str) -> Dict[str, str]:
    """Parse cookie string into a dict."""
    cookies = {}
    for cookie in cookies_str.split("; "):
        try:
            parts = cookie.split("=", 1)
            if len(parts) == 2:
                cookies[parts[0]] = parts[1]
        except Exception:
            continue
    return cookies


def generate_mid() -> str:
    """Generate a message ID."""
    random_part = int(1000 * random.random())
    timestamp = int(time.time() * 1000)
    return f"{random_part}{timestamp} 0"


def generate_uuid() -> str:
    """Generate a UUID-like identifier."""
    timestamp = int(time.time() * 1000)
    return f"-{timestamp}1"


def generate_device_id(user_id: str) -> str:
    """Generate a device ID string."""
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    result: List[str] = []

    for i in range(36):
        if i in (8, 13, 18, 23):
            result.append("-")
        elif i == 14:
            result.append("4")
        else:
            if i == 19:
                rand_val = int(16 * random.random())
                result.append(chars[(rand_val & 0x3) | 0x8])
            else:
                rand_val = int(16 * random.random())
                result.append(chars[rand_val])

    return "".join(result) + "-" + user_id


def generate_sign(t: str, token: str, data: str) -> str:
    """Generate an API signature."""
    app_key = "34839810"
    msg = f"{token}&{t}&{app_key}&{data}"
    return hashlib.md5(msg.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# MessagePack decoder (pure Python, no external dependency)
# ---------------------------------------------------------------------------

class MessagePackDecoder:
    """Minimal pure-Python MessagePack decoder."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.length = len(data)

    def read_byte(self) -> int:
        if self.pos >= self.length:
            raise ValueError("Unexpected end of data")
        byte = self.data[self.pos]
        self.pos += 1
        return byte

    def read_bytes(self, count: int) -> bytes:
        if self.pos + count > self.length:
            raise ValueError("Unexpected end of data")
        result = self.data[self.pos : self.pos + count]
        self.pos += count
        return result

    def read_uint8(self) -> int:
        return self.read_byte()

    def read_uint16(self) -> int:
        return struct.unpack(">H", self.read_bytes(2))[0]

    def read_uint32(self) -> int:
        return struct.unpack(">I", self.read_bytes(4))[0]

    def read_uint64(self) -> int:
        return struct.unpack(">Q", self.read_bytes(8))[0]

    def read_int8(self) -> int:
        return struct.unpack(">b", self.read_bytes(1))[0]

    def read_int16(self) -> int:
        return struct.unpack(">h", self.read_bytes(2))[0]

    def read_int32(self) -> int:
        return struct.unpack(">i", self.read_bytes(4))[0]

    def read_int64(self) -> int:
        return struct.unpack(">q", self.read_bytes(8))[0]

    def read_float32(self) -> float:
        return struct.unpack(">f", self.read_bytes(4))[0]

    def read_float64(self) -> float:
        return struct.unpack(">d", self.read_bytes(8))[0]

    def read_string(self, length: int) -> str:
        return self.read_bytes(length).decode("utf-8")

    def decode_value(self) -> Any:
        if self.pos >= self.length:
            raise ValueError("Unexpected end of data")

        fmt = self.read_byte()

        # Positive fixint
        if fmt <= 0x7F:
            return fmt

        # Fixmap
        if 0x80 <= fmt <= 0x8F:
            return self._decode_map(fmt & 0x0F)

        # Fixarray
        if 0x90 <= fmt <= 0x9F:
            return self._decode_array(fmt & 0x0F)

        # Fixstr
        if 0xA0 <= fmt <= 0xBF:
            return self.read_string(fmt & 0x1F)

        # nil / bool
        if fmt == 0xC0:
            return None
        if fmt == 0xC2:
            return False
        if fmt == 0xC3:
            return True

        # bin 8/16/32
        if fmt == 0xC4:
            return self.read_bytes(self.read_uint8())
        if fmt == 0xC5:
            return self.read_bytes(self.read_uint16())
        if fmt == 0xC6:
            return self.read_bytes(self.read_uint32())

        # float 32/64
        if fmt == 0xCA:
            return self.read_float32()
        if fmt == 0xCB:
            return self.read_float64()

        # uint 8/16/32/64
        if fmt == 0xCC:
            return self.read_uint8()
        if fmt == 0xCD:
            return self.read_uint16()
        if fmt == 0xCE:
            return self.read_uint32()
        if fmt == 0xCF:
            return self.read_uint64()

        # int 8/16/32/64
        if fmt == 0xD0:
            return self.read_int8()
        if fmt == 0xD1:
            return self.read_int16()
        if fmt == 0xD2:
            return self.read_int32()
        if fmt == 0xD3:
            return self.read_int64()

        # str 8/16/32
        if fmt == 0xD9:
            return self.read_string(self.read_uint8())
        if fmt == 0xDA:
            return self.read_string(self.read_uint16())
        if fmt == 0xDB:
            return self.read_string(self.read_uint32())

        # array 16/32
        if fmt == 0xDC:
            return self._decode_array(self.read_uint16())
        if fmt == 0xDD:
            return self._decode_array(self.read_uint32())

        # map 16/32
        if fmt == 0xDE:
            return self._decode_map(self.read_uint16())
        if fmt == 0xDF:
            return self._decode_map(self.read_uint32())

        # Negative fixint
        if fmt >= 0xE0:
            return fmt - 256

        raise ValueError(f"Unknown format byte: 0x{fmt:02x}")

    def _decode_array(self, size: int) -> List[Any]:
        return [self.decode_value() for _ in range(size)]

    def _decode_map(self, size: int) -> Dict[Any, Any]:
        result: Dict[Any, Any] = {}
        for _ in range(size):
            key = self.decode_value()
            result[key] = self.decode_value()
        return result

    def decode(self) -> Any:
        try:
            return self.decode_value()
        except Exception:
            return base64.b64encode(self.data).decode("utf-8")


def decrypt(data: str) -> str:
    """Decrypt a Xianyu IM message payload.

    The payload is base64-encoded MessagePack. Returns a JSON string.
    """
    try:
        # Clean to valid base64 characters
        cleaned = "".join(c for c in data if c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
        while len(cleaned) % 4 != 0:
            cleaned += "="

        try:
            decoded_bytes = base64.b64decode(cleaned)
        except Exception as exc:
            return json.dumps({"error": f"Base64 decode failed: {exc}", "raw_data": data})

        # Try MessagePack decode
        try:
            decoder = MessagePackDecoder(decoded_bytes)
            result = decoder.decode()

            def _json_serializer(obj: Any) -> Any:
                if isinstance(obj, bytes):
                    try:
                        return obj.decode("utf-8")
                    except Exception:
                        return base64.b64encode(obj).decode("utf-8")
                if hasattr(obj, "__dict__"):
                    return obj.__dict__
                return str(obj)

            return json.dumps(result, ensure_ascii=False, default=_json_serializer)

        except Exception as exc:
            # Fallback: try raw UTF-8
            try:
                return json.dumps({"text": decoded_bytes.decode("utf-8")})
            except Exception:
                return json.dumps({"hex": decoded_bytes.hex(), "error": f"Decode failed: {exc}"})

    except Exception as exc:
        return json.dumps({"error": f"Decrypt failed: {exc}", "raw_data": data})
