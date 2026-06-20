"""Audio conversion helpers for Twilio and Gemini."""

from __future__ import annotations

import base64
import math
import sys
from array import array
from typing import Iterable

BIAS = 0x84
CLIP = 32635
SEGMENT_ENDS = (0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF, 0x3FFF, 0x7FFF)


def decode_base64_payload(payload: str) -> bytes:
    """Decode a base64 payload into raw bytes."""
    return base64.b64decode(payload.encode("ascii"))


def encode_base64_payload(payload: bytes) -> str:
    """Encode raw bytes into ASCII base64."""
    return base64.b64encode(payload).decode("ascii")


def ulaw_bytes_to_pcm16le(payload: bytes) -> bytes:
    """Convert G.711 mu-law bytes into PCM16LE."""
    samples = array("h")
    for byte_value in payload:
        samples.append(_ulaw_decode_sample(byte_value))
    return _samples_to_bytes(samples)


def pcm16le_to_ulaw_bytes(payload: bytes) -> bytes:
    """Convert PCM16LE into G.711 mu-law bytes."""
    samples = _bytes_to_samples(payload)
    return bytes(_ulaw_encode_sample(sample) for sample in samples)


def resample_pcm16le(payload: bytes, input_rate: int, output_rate: int) -> bytes:
    """Resample PCM16LE with simple linear interpolation."""
    if input_rate <= 0 or output_rate <= 0:
        raise ValueError("Sample rates must be positive integers.")
    if input_rate == output_rate or not payload:
        return payload

    source = _bytes_to_samples(payload)
    if len(source) == 1:
        repeated = array("h", [source[0]] * max(1, output_rate // input_rate))
        return _samples_to_bytes(repeated)

    output_length = max(1, int(math.ceil(len(source) * output_rate / input_rate)))
    result = array("h")

    for index in range(output_length):
        position = index * (input_rate / output_rate)
        left_index = min(int(position), len(source) - 1)
        right_index = min(left_index + 1, len(source) - 1)
        fraction = position - left_index
        left_value = source[left_index]
        right_value = source[right_index]
        interpolated = int(round((1.0 - fraction) * left_value + fraction * right_value))
        result.append(_clamp_int16(interpolated))

    return _samples_to_bytes(result)


def chunk_bytes(payload: bytes, chunk_size: int) -> Iterable[bytes]:
    """Yield fixed-size chunks from a byte payload."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    for offset in range(0, len(payload), chunk_size):
        yield payload[offset : offset + chunk_size]


def pcm16le_average_amplitude(payload: bytes) -> float:
    """Return the average absolute sample amplitude for PCM16LE audio."""
    samples = _bytes_to_samples(payload)
    if not samples:
        return 0.0
    return sum(abs(sample) for sample in samples) / len(samples)


def _ulaw_decode_sample(byte_value: int) -> int:
    value = (~byte_value) & 0xFF
    sign = value & 0x80
    exponent = (value >> 4) & 0x07
    mantissa = value & 0x0F
    sample = ((mantissa << 3) + BIAS) << exponent
    sample -= BIAS
    return -sample if sign else sample


def _ulaw_encode_sample(sample: int) -> int:
    sample = _clamp_int16(sample)
    sign = 0x80 if sample < 0 else 0x00
    magnitude = -sample if sample < 0 else sample
    magnitude = min(magnitude, CLIP)
    magnitude += BIAS

    exponent = 7
    for index, threshold in enumerate(SEGMENT_ENDS):
        if magnitude <= threshold:
            exponent = index
            break

    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def _bytes_to_samples(payload: bytes) -> array:
    samples = array("h")
    samples.frombytes(payload)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _samples_to_bytes(samples: array) -> bytes:
    encoded = array("h", samples)
    if sys.byteorder != "little":
        encoded.byteswap()
    return encoded.tobytes()


def _clamp_int16(value: int) -> int:
    return max(-32768, min(32767, value))
