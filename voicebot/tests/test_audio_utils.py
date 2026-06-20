from __future__ import annotations

from voicebot.src.utils.audio import (
    chunk_bytes,
    pcm16le_average_amplitude,
    pcm16le_to_ulaw_bytes,
    resample_pcm16le,
    ulaw_bytes_to_pcm16le,
)


def test_mulaw_round_trip_preserves_audio_shape() -> None:
    source = (1000).to_bytes(2, "little", signed=True) * 32
    encoded = pcm16le_to_ulaw_bytes(source)
    decoded = ulaw_bytes_to_pcm16le(encoded)
    assert len(encoded) == 32
    assert len(decoded) == len(source)


def test_resample_changes_output_length() -> None:
    source = (500).to_bytes(2, "little", signed=True) * 24
    resampled = resample_pcm16le(source, input_rate=24000, output_rate=8000)
    assert len(resampled) < len(source)


def test_chunk_bytes_splits_payload() -> None:
    chunks = list(chunk_bytes(b"abcdefgh", 3))
    assert chunks == [b"abc", b"def", b"gh"]


def test_pcm16le_average_amplitude_reflects_signal_strength() -> None:
    quiet = (200).to_bytes(2, "little", signed=True) * 32
    loud = (4000).to_bytes(2, "little", signed=True) * 32
    assert pcm16le_average_amplitude(loud) > pcm16le_average_amplitude(quiet)
