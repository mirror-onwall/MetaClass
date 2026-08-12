from __future__ import annotations

import hashlib
import json
import ssl
import struct
import subprocess
import time
import wave
from functools import lru_cache
from pathlib import Path
from shutil import which
from urllib import error, request

import certifi
import imageio_ffmpeg

from metaclass.infrastructure.providers.base import TTSProvider
from metaclass.infrastructure.providers.fake import FakeTTSProvider

DEFAULT_STUDENT_ROLES = (
    "classroom_atmosphere_regulator",
    "deep_thinker",
    "note_taker",
    "researcher",
    "foundation_weak",
    "silent_observer",
    "concept_confused",
    "practical_applier",
)


@lru_cache(maxsize=1)
def _ffmpeg_executable() -> str:
    system_ffmpeg = which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    if not Path(bundled_ffmpeg).is_file():
        raise RuntimeError("ffmpeg is unavailable for TTS audio conversion")
    return bundled_ffmpeg


def _normalize_wav_header(audio: bytes) -> bytes:
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return audio
    data_offset = audio.find(b"data", 12)
    if data_offset < 0 or data_offset + 8 > len(audio):
        return audio
    normalized = bytearray(audio)
    struct.pack_into("<I", normalized, 4, len(normalized) - 8)
    struct.pack_into("<I", normalized, data_offset + 4, len(normalized) - data_offset - 8)
    return bytes(normalized)


def _write_standard_wav(audio: bytes, output: Path) -> float:
    output.parent.mkdir(parents=True, exist_ok=True)
    source = output.with_suffix(".source-audio")
    try:
        if len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
            output.write_bytes(_normalize_wav_header(audio))
        else:
            source.write_bytes(audio)
            try:
                converted = subprocess.run(
                    [
                        _ffmpeg_executable(),
                        "-y",
                        "-loglevel",
                        "error",
                        "-i",
                        str(source),
                        "-vn",
                        "-c:a",
                        "pcm_s16le",
                        str(output),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except (FileNotFoundError, RuntimeError) as exc:
                raise RuntimeError(
                    "TTS returned non-WAV audio, but ffmpeg is unavailable for conversion"
                ) from exc
            if converted.returncode != 0:
                detail = converted.stderr.strip()[-500:]
                raise RuntimeError(
                    f"TTS service returned unsupported audio data: {detail or 'unknown format'}"
                )

        with wave.open(str(output), "rb") as wav:
            frame_size = wav.getnchannels() * wav.getsampwidth()
            frame_count = 0
            while chunk := wav.readframes(65_536):
                frame_count += len(chunk) // frame_size
            return frame_count / wav.getframerate()
    except (wave.Error, ZeroDivisionError) as exc:
        raise RuntimeError("TTS service did not return a valid audio file") from exc
    finally:
        source.unlink(missing_ok=True)
        if not output.exists() or output.stat().st_size == 0:
            output.unlink(missing_ok=True)


class OpenAICompatibleTTSProvider:
    """Text-to-speech client for services exposing POST /audio/speech."""

    name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        teacher_voice: str,
        student_voices: list[str],
        timeout_seconds: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.teacher_voice = teacher_voice
        self.student_voices = student_voices or [teacher_voice]
        self.timeout_seconds = timeout_seconds
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    def synthesize(self, text: str, output: Path, voice: str | None = None) -> float:
        payload = {
            "model": self.model,
            "input": text,
            "voice": self._resolve_voice(voice),
            "response_format": "wav",
        }
        req = request.Request(
            f"{self.base_url}/audio/speech",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        audio: bytes | None = None
        for attempt in range(2):
            try:
                with request.urlopen(
                    req,
                    timeout=self.timeout_seconds,
                    context=self.ssl_context,
                ) as response:
                    audio = response.read()
                break
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"TTS request failed: HTTP {exc.code} {detail}") from exc
            except (TimeoutError, error.URLError) as exc:
                if attempt == 0:
                    time.sleep(1)
                    continue
                reason = getattr(exc, "reason", exc)
                raise RuntimeError(f"TTS request failed after retry: {reason}") from exc

        if audio is None:
            raise RuntimeError("TTS request did not return audio")

        try:
            return _write_standard_wav(audio, output)
        except RuntimeError:
            output.unlink(missing_ok=True)
            raise

    def _resolve_voice(self, voice: str | None) -> str:
        if not voice or voice == "teacher":
            return self.teacher_voice
        if voice == "student" or voice.startswith("student_"):
            role = voice.removeprefix("student_")
            if role in DEFAULT_STUDENT_ROLES:
                return self.student_voices[
                    DEFAULT_STUDENT_ROLES.index(role) % len(self.student_voices)
                ]
            digest = hashlib.sha256(voice.encode("utf-8")).digest()
            return self.student_voices[int.from_bytes(digest[:2], "big") % len(self.student_voices)]
        return voice


class MiniMaxTTSProvider:
    """Text-to-speech client for MiniMax T2A v2 compatible endpoints."""

    name = "minimax"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        teacher_voice: str,
        student_voices: list[str],
        timeout_seconds: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.teacher_voice = teacher_voice
        self.student_voices = student_voices or [teacher_voice]
        self.timeout_seconds = timeout_seconds
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    def synthesize(self, text: str, output: Path, voice: str | None = None) -> float:
        payload = {
            "model": self.model,
            "text": text,
            "stream": False,
            "language_boost": "Chinese",
            "output_format": "hex",
            "voice_setting": {
                "voice_id": self._resolve_voice(voice),
                "speed": 1.0,
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32_000,
                "bitrate": 128_000,
                "format": "mp3",
                "channel": 1,
            },
        }
        req = request.Request(
            f"{self.base_url}/v1/t2a_v2",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        response_payload: dict[str, object] | None = None
        for attempt in range(2):
            try:
                with request.urlopen(
                    req,
                    timeout=self.timeout_seconds,
                    context=self.ssl_context,
                ) as response:
                    response_payload = json.loads(response.read().decode("utf-8"))
                break
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if attempt == 0 and (exc.code == 429 or 500 <= exc.code < 600):
                    time.sleep(1.0)
                    continue
                raise RuntimeError(f"MiniMax TTS request failed: HTTP {exc.code} {detail}") from exc
            except (TimeoutError, error.URLError) as exc:
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                reason = getattr(exc, "reason", exc)
                raise RuntimeError(f"MiniMax TTS request failed after retry: {reason}") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("MiniMax TTS returned invalid JSON") from exc

        if response_payload is None:
            raise RuntimeError("MiniMax TTS request did not return a response")
        base_resp = response_payload.get("base_resp")
        if isinstance(base_resp, dict) and base_resp.get("status_code") not in {None, 0}:
            raise RuntimeError(
                f"MiniMax TTS request failed: {base_resp.get('status_msg', 'unknown error')}"
            )
        data = response_payload.get("data")
        audio_hex = data.get("audio") if isinstance(data, dict) else None
        if not isinstance(audio_hex, str) or not audio_hex:
            raise RuntimeError("MiniMax TTS response did not contain audio")
        try:
            audio = bytes.fromhex(audio_hex)
        except ValueError as exc:
            raise RuntimeError("MiniMax TTS returned invalid hex audio") from exc
        return _write_standard_wav(audio, output)

    def _resolve_voice(self, voice: str | None) -> str:
        if not voice or voice == "teacher":
            return self.teacher_voice
        if voice == "student" or voice.startswith("student_"):
            role = voice.removeprefix("student_")
            if role in DEFAULT_STUDENT_ROLES:
                return self.student_voices[
                    DEFAULT_STUDENT_ROLES.index(role) % len(self.student_voices)
                ]
            digest = hashlib.sha256(voice.encode("utf-8")).digest()
            return self.student_voices[int.from_bytes(digest[:2], "big") % len(self.student_voices)]
        return voice


def build_tts_provider(
    *,
    provider: str,
    base_url: str,
    api_key: str | None,
    model: str,
    teacher_voice: str,
    student_voices: list[str],
    timeout_seconds: float,
) -> TTSProvider:
    normalized = provider.strip().lower()
    if normalized in {"", "fake", "none"}:
        return FakeTTSProvider()
    if normalized in {"openai", "openai-compatible", "compatible"}:
        if not api_key:
            raise RuntimeError(
                "METACLASS_TTS_API_KEY or METACLASS_LLM_API_KEY is required for real TTS"
            )
        return OpenAICompatibleTTSProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            teacher_voice=teacher_voice,
            student_voices=student_voices,
            timeout_seconds=timeout_seconds,
        )
    if normalized in {"minimax", "minimax-compatible"}:
        if not api_key:
            raise RuntimeError("METACLASS_TTS_API_KEY is required for MiniMax TTS")
        return MiniMaxTTSProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            teacher_voice=teacher_voice,
            student_voices=student_voices,
            timeout_seconds=timeout_seconds,
        )
    raise RuntimeError(f"Unsupported METACLASS_TTS_PROVIDER: {provider}")
