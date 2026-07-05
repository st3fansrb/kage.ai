"""Teste pentru transcrierea vocală locală (WP6).

Acoperă: rezolvarea binarului whisper.cpp, degradarea grațioasă când binarul/modelul
lipsesc (503), calea fericită de transcriere (cu și fără ffmpeg), și endpoint-ul
OpenAI-compatible POST /v1/audio/transcriptions prin TestClient.

Toate rulează cu subprocess-uri mock-uite; niciun apel real la whisper.cpp/ffmpeg.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import orchestrator


@pytest.fixture
def client(monkeypatch):
    # Dezactivează auth-ul (mașina reală poate avea api_token în config)
    monkeypatch.setattr(orchestrator, "_get_api_token", lambda: "")
    return TestClient(orchestrator.app)


class _FakeProc:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self._stdout, self._stderr, self.returncode = stdout, stderr, returncode

    async def communicate(self):
        return self._stdout, self._stderr


def _install_fake_subprocess(monkeypatch, results):
    """Înlocuiește asyncio.create_subprocess_exec cu un fake care întoarce pe rând
    `results`. Un fake de ffmpeg creează fișierul WAV de ieșire (ultimul arg)."""
    calls = []
    it = iter(results)

    async def _fake(*args, **kwargs):
        calls.append(args)
        if "ffmpeg" in str(args[0]):
            try:
                Path(args[-1]).write_bytes(b"RIFF0000WAVE")
            except OSError:
                pass
        return next(it)

    monkeypatch.setattr(orchestrator.asyncio, "create_subprocess_exec", _fake)
    return calls


# ── Rezolvarea binarului ──────────────────────────────────────────────────────

def test_resolve_whisper_bin_missing(monkeypatch):
    monkeypatch.setattr(orchestrator, "WHISPER_BIN", "whisper-cli-inexistent-xyz")
    monkeypatch.setattr(orchestrator.shutil, "which", lambda name: None)
    assert orchestrator._resolve_whisper_bin() is None


def test_resolve_whisper_bin_from_path(monkeypatch):
    monkeypatch.setattr(orchestrator, "WHISPER_BIN", "whisper-cli")
    monkeypatch.setattr(orchestrator.shutil, "which", lambda name: "/opt/homebrew/bin/whisper-cli")
    assert orchestrator._resolve_whisper_bin() == "/opt/homebrew/bin/whisper-cli"


def test_resolve_whisper_bin_absolute(monkeypatch, tmp_path):
    fake = tmp_path / "whisper-cli"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setattr(orchestrator, "WHISPER_BIN", str(fake))
    assert orchestrator._resolve_whisper_bin() == str(fake)


# ── Degradare grațioasă ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transcribe_raises_when_bin_missing(monkeypatch):
    monkeypatch.setattr(orchestrator, "_resolve_whisper_bin", lambda: None)
    with pytest.raises(orchestrator._WhisperUnavailable):
        await orchestrator._transcribe_audio(b"x", ".ogg")


@pytest.mark.asyncio
async def test_transcribe_raises_when_model_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "_resolve_whisper_bin", lambda: str(tmp_path / "whisper-cli"))
    monkeypatch.setattr(orchestrator, "WHISPER_MODEL", str(tmp_path / "nu-exista.bin"))
    with pytest.raises(orchestrator._WhisperUnavailable):
        await orchestrator._transcribe_audio(b"x", ".ogg")


# ── Calea fericită ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transcribe_no_ffmpeg_returns_text(monkeypatch, tmp_path):
    model = tmp_path / "model.bin"
    model.write_bytes(b"ggml")
    monkeypatch.setattr(orchestrator, "_resolve_whisper_bin", lambda: "/usr/bin/whisper-cli")
    monkeypatch.setattr(orchestrator, "WHISPER_MODEL", str(model))
    monkeypatch.setattr(orchestrator.shutil, "which", lambda name: None)  # fără ffmpeg
    calls = _install_fake_subprocess(monkeypatch, [_FakeProc(stdout=b" Salut, e un test.\n")])

    text = await orchestrator._transcribe_audio(b"ogg-bytes", ".ogg")
    assert text == "Salut, e un test."
    assert len(calls) == 1                      # doar whisper, fără ffmpeg
    assert "whisper-cli" in calls[0][0]


@pytest.mark.asyncio
async def test_transcribe_with_ffmpeg_converts_then_transcribes(monkeypatch, tmp_path):
    model = tmp_path / "model.bin"
    model.write_bytes(b"ggml")
    monkeypatch.setattr(orchestrator, "_resolve_whisper_bin", lambda: "/usr/bin/whisper-cli")
    monkeypatch.setattr(orchestrator, "WHISPER_MODEL", str(model))
    monkeypatch.setattr(orchestrator.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    calls = _install_fake_subprocess(
        monkeypatch, [_FakeProc(returncode=0), _FakeProc(stdout=b"Buna dimineata.\n")]
    )

    text = await orchestrator._transcribe_audio(b"ogg-bytes", ".ogg")
    assert text == "Buna dimineata."
    assert len(calls) == 2                       # ffmpeg + whisper
    assert "ffmpeg" in str(calls[0][0])
    # whisper primește fișierul WAV convertit, nu OGG-ul brut
    whisper_args = calls[1]
    assert any(str(a).endswith(".wav") for a in whisper_args)


@pytest.mark.asyncio
async def test_transcribe_nonzero_returncode_raises(monkeypatch, tmp_path):
    model = tmp_path / "model.bin"
    model.write_bytes(b"ggml")
    monkeypatch.setattr(orchestrator, "_resolve_whisper_bin", lambda: "/usr/bin/whisper-cli")
    monkeypatch.setattr(orchestrator, "WHISPER_MODEL", str(model))
    monkeypatch.setattr(orchestrator.shutil, "which", lambda name: None)
    _install_fake_subprocess(monkeypatch, [_FakeProc(stderr=b"boom", returncode=2)])

    with pytest.raises(RuntimeError):
        await orchestrator._transcribe_audio(b"x", ".ogg")


# ── Endpoint ──────────────────────────────────────────────────────────────────

def test_endpoint_missing_file_field(client):
    resp = client.post("/v1/audio/transcriptions", files={"nope": ("x.txt", b"data")})
    assert resp.status_code == 400


def test_endpoint_empty_file(client):
    resp = client.post("/v1/audio/transcriptions", files={"file": ("v.ogg", b"", "application/ogg")})
    assert resp.status_code == 400


def test_endpoint_whisper_unavailable_returns_503(client, monkeypatch):
    async def _boom(*a, **k):
        raise orchestrator._WhisperUnavailable("neinstalat")

    monkeypatch.setattr(orchestrator, "_transcribe_audio", _boom)
    resp = client.post("/v1/audio/transcriptions", files={"file": ("v.ogg", b"opus-bytes", "application/ogg")})
    assert resp.status_code == 503
    assert "neinstalat" in resp.json()["error"]


def test_endpoint_happy_path(client, monkeypatch):
    async def _ok(audio_bytes, src_suffix=".ogg"):
        assert audio_bytes == b"opus-bytes"
        assert src_suffix == ".ogg"
        return "text transcris local"

    monkeypatch.setattr(orchestrator, "_transcribe_audio", _ok)
    resp = client.post("/v1/audio/transcriptions", files={"file": ("v.ogg", b"opus-bytes", "application/ogg")})
    assert resp.status_code == 200
    assert resp.json() == {"text": "text transcris local"}
