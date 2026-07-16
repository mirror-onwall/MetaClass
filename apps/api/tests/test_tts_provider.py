import io
import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from metaclass.infrastructure.providers.tts import (
    MiniMaxTTSProvider,
    _ffmpeg_executable,
    _write_standard_wav,
)


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


def test_ffmpeg_falls_back_to_imageio_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundled = tmp_path / "ffmpeg-bundled"
    bundled.write_bytes(b"binary")
    _ffmpeg_executable.cache_clear()
    monkeypatch.setattr("metaclass.infrastructure.providers.tts.which", lambda _name: None)
    monkeypatch.setattr(
        "metaclass.infrastructure.providers.tts.imageio_ffmpeg.get_ffmpeg_exe",
        lambda: str(bundled),
    )

    assert _ffmpeg_executable() == str(bundled)
    _ffmpeg_executable.cache_clear()


def test_minimax_tts_uses_role_voice_and_decodes_hex_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "data": {"audio": b"simulated mp3".hex(), "status": 2},
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                }
            ).encode()

    def fake_urlopen(req: object, **_kwargs: object) -> FakeResponse:
        captured["url"] = getattr(req, "full_url")
        captured["payload"] = json.loads(getattr(req, "data").decode())
        return FakeResponse()

    def fake_write(audio: bytes, output: Path) -> float:
        captured["audio"] = audio
        output.write_bytes(make_wav())
        return 0.1

    monkeypatch.setattr("metaclass.infrastructure.providers.tts.request.urlopen", fake_urlopen)
    monkeypatch.setattr("metaclass.infrastructure.providers.tts._write_standard_wav", fake_write)
    provider = MiniMaxTTSProvider(
        base_url="https://example.test/minimax",
        api_key="test-key",
        model="speech-2.8-turbo",
        teacher_voice="teacher-voice",
        student_voices=["student-one", "student-two"],
    )

    duration = provider.synthesize(
        "你好，欢迎来到课堂。",
        tmp_path / "audio.wav",
        "student_deep_thinker",
    )

    assert duration == pytest.approx(0.1)
    assert captured["url"] == "https://example.test/minimax/v1/t2a_v2"
    assert captured["audio"] == b"simulated mp3"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "speech-2.8-turbo"
    assert payload["voice_setting"]["voice_id"] == "student-two"
