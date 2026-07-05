"""WP-G1 — policy as code (D7): chat cu capability minimă, task/sysrun complet."""
import orchestrator as o


def test_chat_policy_has_no_bash():
    """Un mesaj de chat normal nu trebuie să poată spawna claude cu Bash (D7)."""
    flags = o._policy_cli_flags("chat")
    allowed = _allowed_tools(flags)
    assert "Bash" not in allowed
    assert "Write" not in allowed
    assert "Edit" not in allowed
    # read-only rămâne util pentru context
    assert "Read" in allowed
    # Bash e explicit interzis
    assert "Bash" in _disallowed_tools(flags)


def test_task_policy_has_full_tools():
    allowed = _allowed_tools(o._policy_cli_flags("task"))
    for t in ("Bash", "Read", "Write", "Edit"):
        assert t in allowed


def test_sysrun_policy_has_full_tools():
    assert "Bash" in _allowed_tools(o._policy_cli_flags("sysrun"))


def test_scheduled_policy_is_readonly():
    flags = o._policy_cli_flags("scheduled")
    assert "Bash" not in _allowed_tools(flags)


def test_permission_mode_present():
    flags = o._policy_cli_flags("chat")
    assert "--permission-mode" in flags


def test_fallback_when_policy_file_missing(monkeypatch, tmp_path):
    """Dacă policy.yaml lipsește, fallback-ul intern păstrează chat fără Bash."""
    monkeypatch.setattr(o, "POLICY_FILE", tmp_path / "does-not-exist.yaml")
    flags = o._policy_cli_flags("chat")
    assert "Bash" not in _allowed_tools(flags)
    assert "Bash" in _allowed_tools(o._policy_cli_flags("task"))


def test_empty_tools_omits_allowedtools(monkeypatch, tmp_path):
    """`tools: []` → niciun --allowedTools (zero unelte)."""
    pol = tmp_path / "policy.yaml"
    pol.write_text("run_types:\n  chat:\n    tools: []\n    permission_mode: auto\n", encoding="utf-8")
    monkeypatch.setattr(o, "POLICY_FILE", pol)
    flags = o._policy_cli_flags("chat")
    assert "--allowedTools" not in flags


# ── helpers ──
def _allowed_tools(flags):
    if "--allowedTools" not in flags:
        return set()
    return set(flags[flags.index("--allowedTools") + 1].split(","))


def _disallowed_tools(flags):
    if "--disallowedTools" not in flags:
        return set()
    return set(flags[flags.index("--disallowedTools") + 1].split(","))
