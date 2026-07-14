import io
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from metaclass.infrastructure.providers.tts import _write_standard_wav


def make_wav(duration_seconds: float = 0.1, sample_rate: int = 8_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * round(duration_seconds * sample_rate))
    return buffer.getvalue()


def test_non_wav_tts_response_is_converted_with_ffmpeg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    converted_wav = make_wav()

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        source = Path(command[command.index("-i") + 1])
        assert source.read_bytes() == b"ID3 simulated mp3 data"
        Path(command[-1]).write_bytes(converted_wav)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(
        "metaclass.infrastructure.providers.tts.subprocess.run",
        fake_run,
    )
    output = tmp_path / "audio.wav"

    duration = _write_standard_wav(b"ID3 simulated mp3 data", output)

    assert output.read_bytes() == converted_wav
    assert duration == pytest.approx(0.1)
    assert not output.with_suffix(".source-audio").exists()
