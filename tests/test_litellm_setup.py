"""#5 — LiteLLM doar pe loopback și fără master_key real în git.

setup.sh rulează într-un sandbox: un `.venv` fals (pip no-op, python3 = interpretorul
testelor) și un PATH minim fără python3.12.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_KEY = "sk-orchestrator-local"
_TOOLS = ("bash", "sed", "cp", "cut", "dirname", "chmod", "grep", "cat")


def _master_key(yaml_text: str) -> str:
    m = re.search(r"^\s*master_key:\s*(\S+)\s*$", yaml_text, re.MULTILINE)
    assert m, "master_key lipsește din config"
    return m.group(1)


def _sandbox(tmp_path: Path, kage_config: dict | None = None) -> Path:
    d = tmp_path / "kage"
    (d / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "setup.sh", d / "scripts" / "setup.sh")
    for name in ("litellm_config.example.yaml", "kage_config.example.json",
                 "risk_settings.example.json"):
        shutil.copy(ROOT / name, d / name)
    (d / "requirements.txt").write_text("")
    if kage_config is not None:
        (d / "kage_config.json").write_text(json.dumps(kage_config))

    venv_bin = d / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "pip").write_text("#!/bin/sh\nexit 0\n")
    (venv_bin / "pip").chmod(0o755)
    (venv_bin / "python3").symlink_to(sys.executable)

    tools = tmp_path / "bin"
    tools.mkdir()
    for t in _TOOLS:
        found = shutil.which(t)
        if not found:
            pytest.skip(f"{t} lipsește din mediu")
        (tools / t).symlink_to(found)
    (tools / "python3").symlink_to(sys.executable)
    return d


def _run_setup(d: Path) -> subprocess.CompletedProcess:
    env = {"PATH": str(d.parent / "bin"), "HOME": str(d.parent)}
    r = subprocess.run([str(d.parent / "bin" / "bash"), str(d / "scripts" / "setup.sh")],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def test_setup_generates_random_master_key_shared_with_orchestrator(tmp_path):
    d = _sandbox(tmp_path)
    _run_setup(d)

    key = _master_key((d / "litellm_config.yaml").read_text())
    assert key != PUBLIC_KEY and key.startswith("sk-kage-") and len(key) >= 40
    cfg = json.loads((d / "kage_config.json").read_text())
    # orchestrator.py citește litellm_key de la rădăcină; providers e citit de modulele din kage-labs.
    assert cfg["litellm_key"] == key
    assert cfg["providers"]["litellm_key"] == key
    assert (d / "litellm_config.yaml").stat().st_mode & 0o077 == 0


def test_setup_keys_differ_between_installs(tmp_path):
    a = _sandbox(tmp_path / "a")
    b = _sandbox(tmp_path / "b")
    _run_setup(a)
    _run_setup(b)
    assert (_master_key((a / "litellm_config.yaml").read_text())
            != _master_key((b / "litellm_config.yaml").read_text()))


def test_setup_keeps_existing_custom_key(tmp_path):
    cfg = json.loads((ROOT / "kage_config.example.json").read_text())
    cfg["providers"]["litellm_key"] = "sk-my-own-key-123"
    d = _sandbox(tmp_path, kage_config=cfg)
    _run_setup(d)
    assert _master_key((d / "litellm_config.yaml").read_text()) == "sk-my-own-key-123"


def test_setup_warns_on_existing_public_key(tmp_path):
    d = _sandbox(tmp_path)
    (d / "litellm_config.yaml").write_text(f"general_settings:\n  master_key: {PUBLIC_KEY}\n")
    r = _run_setup(d)
    assert PUBLIC_KEY in r.stdout
    assert _master_key((d / "litellm_config.yaml").read_text()) == PUBLIC_KEY  # nu-l suprascrie


def test_example_config_has_only_placeholder():
    assert _master_key((ROOT / "litellm_config.example.yaml").read_text()) == "KAGE_LITELLM_KEY"


def test_litellm_config_not_tracked():
    if not (ROOT / ".git").exists() or not shutil.which("git"):
        pytest.skip("nu e un checkout git")
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "litellm_config.yaml"],
                             capture_output=True, text=True).stdout.strip()
    assert tracked == ""
    ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", "litellm_config.yaml"])
    assert ignored.returncode == 0


@pytest.mark.parametrize("script", ["start_all.sh", "scripts/start_litellm.sh"])
def test_litellm_binds_loopback_only(script):
    text = (ROOT / script).read_text()
    block = text[text.index("litellm"):]
    block = block[:block.index("litellm.log")] if "litellm.log" in block else block
    assert "--host 127.0.0.1" in block
    assert "0.0.0.0" not in block
