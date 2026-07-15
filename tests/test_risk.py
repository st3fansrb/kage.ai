"""Teste pentru risk gate v2 (WP2).

Acoperă: curățarea pattern-urilor (D6), axa 2 (downgrade High→Medium la instrucție
explicită) și mutarea High în fluxul de aprobare (nu deny direct). Testele pe main()
sunt hermetice — rețeaua (register/ntfy/poll) și logul în vault sunt mock-uite, deci
NU ating serverul real.
"""
import contextlib
import io
import json
import sys

import risk_hook


# ── WP-SD: extinderea allowed_task_roots cu worktree-urile de misiune ─────────

def test_extend_roots_noop_when_confinement_disabled():
    # roots gol = confinement dezactivat — NU trebuie activat doar ca să încapă worktree-ul.
    assert risk_hook._extend_roots_with_worktrees([], "/home/x/.kage-worktrees") == []


def test_extend_roots_appends_when_active():
    out = risk_hook._extend_roots_with_worktrees(["/a", "/b"], "/home/x/.kage-worktrees")
    assert out == ["/a", "/b", "/home/x/.kage-worktrees"]


def test_extend_roots_no_duplicate_if_already_present():
    out = risk_hook._extend_roots_with_worktrees(["/a", "/home/x/.kage-worktrees"], "/home/x/.kage-worktrees")
    assert out == ["/a", "/home/x/.kage-worktrees"]


def test_path_in_allowed_roots_covers_worktree_subpath():
    roots = risk_hook._extend_roots_with_worktrees(["/a"], "/home/x/.kage-worktrees")
    assert risk_hook._path_in_allowed_roots("/home/x/.kage-worktrees/some-slug/file.py", roots)
    assert not risk_hook._path_in_allowed_roots("/etc/passwd", roots)


# ── evaluate_risk: pattern cleanup + axa 2 ────────────────────────────────────

def _lvl(cmd, um=""):
    return risk_hook.evaluate_risk("Bash", {"command": cmd}, um)[0]


def test_redirect_devnull_not_high():
    # `2>/dev/null` era clasificat greșit ca High (D6) — acum e benign
    assert _lvl("echo salut 2>/dev/null") == "Safe"


def test_git_rebase_now_high_not_never():
    assert _lvl("git rebase main") == "High"


def test_git_amend_now_high_not_never():
    assert _lvl("git commit --amend -m x") == "High"


def test_rm_home_still_never():
    assert _lvl("rm -rf ~/") == "Never"


def test_high_downgrades_with_explicit_keyword():
    assert _lvl("rm src/app.py") == "High"
    assert _lvl("rm src/app.py", "șterge src/app.py te rog") == "Medium"


def test_write_active_path_downgrades_with_keyword():
    hi = risk_hook.evaluate_risk("Write", {"file_path": "/proj/src/main.py"}, "")
    lo = risk_hook.evaluate_risk("Write", {"file_path": "/proj/src/main.py"}, "curăță fișierul")
    assert hi[0] == "High"
    assert lo[0] == "Medium"


# ── main(): High intră în flux de aprobare, nu deny direct ─────────────────────

def _run_main(monkeypatch, hook_input, autonomous, confirm_response, user_msg=""):
    monkeypatch.setenv("ORCHESTRATOR_USER_MSG", user_msg)
    monkeypatch.setattr(
        risk_hook, "_load_ntfy_config",
        lambda: {"autonomous_mode": autonomous, "confirm_timeout_secs": 1},
    )
    monkeypatch.setattr(risk_hook, "_get_api_token", lambda: "")
    monkeypatch.setattr(risk_hook, "_send_ntfy", lambda *a, **k: None)
    monkeypatch.setattr(risk_hook, "log_decision", lambda *a, **k: None)
    reg = {"called": False}
    monkeypatch.setattr(
        risk_hook, "_register_with_orchestrator",
        lambda *a, **k: reg.__setitem__("called", True),
    )
    monkeypatch.setattr(risk_hook, "_wait_for_confirm", lambda rid, t: confirm_response)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        risk_hook.main()
    return json.loads(buf.getvalue()), reg


_HIGH_CMD = {"tool_name": "Bash", "tool_input": {"command": "rm src/x.py"}}
_NEVER_CMD = {"tool_name": "Bash", "tool_input": {"command": "rm -rf ~/"}}


def test_high_enters_approval_flow_confirmed(monkeypatch):
    out, reg = _run_main(monkeypatch, _HIGH_CMD, autonomous=True, confirm_response="confirm")
    assert reg["called"] is True, "High trebuie să intre în fluxul de aprobare (register)"
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_high_approval_timeout_denies(monkeypatch):
    out, reg = _run_main(monkeypatch, _HIGH_CMD, autonomous=True, confirm_response="timeout")
    assert reg["called"] is True
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_high_enters_approval_even_without_autonomous(monkeypatch):
    # High nu depinde de autonomous_mode — mereu cere aprobare (nu mai e deny direct)
    out, reg = _run_main(monkeypatch, _HIGH_CMD, autonomous=False, confirm_response="confirm")
    assert reg["called"] is True
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_never_still_deny_direct(monkeypatch):
    out, reg = _run_main(monkeypatch, _NEVER_CMD, autonomous=True, confirm_response="confirm")
    assert reg["called"] is False, "Never nu intră în flux de aprobare — deny direct"
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
