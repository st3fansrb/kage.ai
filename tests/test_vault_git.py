"""WP-G1 — vault sub git: init + commit zilnic, un !save greșit e reversibil."""
import subprocess

import orchestrator as o


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def test_init_and_first_commit(tmp_path):
    (tmp_path / "note.md").write_text("hello", encoding="utf-8")
    msg = o._vault_git_commit(tmp_path)
    assert "commit ok" in msg
    assert (tmp_path / ".git").exists()
    log = _git(tmp_path, "log", "--oneline")
    assert "kage auto-commit" in log.stdout


def test_noop_when_nothing_changed(tmp_path):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    o._vault_git_commit(tmp_path)
    msg = o._vault_git_commit(tmp_path)
    assert "nimic de comis" in msg


def test_save_is_revertible(tmp_path):
    """Un !save greșit al unui agent e reversibil cu git revert (acceptare WP-G1)."""
    good = tmp_path / "profile.md"
    good.write_text("date bune", encoding="utf-8")
    o._vault_git_commit(tmp_path)

    # agentul suprascrie fișierul greșit
    good.write_text("PROSTIE care distruge datele", encoding="utf-8")
    o._vault_git_commit(tmp_path)

    bad_hash = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    rev = _git(tmp_path, "revert", "--no-edit", bad_hash)
    assert rev.returncode == 0
    assert good.read_text(encoding="utf-8") == "date bune"


def test_missing_vault_is_noop(tmp_path):
    msg = o._vault_git_commit(tmp_path / "nu-exista")
    assert "skip" in msg


# ── WP-B: push off-machine pe remote ──────────────────────────────────────────

def test_no_push_when_remote_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(o, "VAULT_GIT_REMOTE", "")
    (tmp_path / "note.md").write_text("x", encoding="utf-8")
    msg = o._vault_git_commit(tmp_path)
    assert "commit ok" in msg and "push" not in msg


def test_push_to_remote_on_commit(tmp_path, monkeypatch):
    """Commit-ul nocturn împinge pe remote-ul configurat (acceptare WP-B)."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))  # bare repo local ca 'GitHub'
    monkeypatch.setattr(o, "VAULT_GIT_REMOTE", str(remote))

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("hello", encoding="utf-8")
    msg = o._vault_git_commit(vault)
    assert "commit ok" in msg and "push ok" in msg

    # remote-ul (bare repo) chiar are commit-ul
    ls = subprocess.run(["git", "--git-dir", str(remote), "log", "--oneline"],
                        capture_output=True, text=True)
    assert "kage auto-commit" in ls.stdout


def test_push_failure_does_not_crash_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(o, "VAULT_GIT_REMOTE", str(tmp_path / "nonexistent.git"))
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("x", encoding="utf-8")
    msg = o._vault_git_commit(vault)
    # commit-ul reușește local; push-ul eșuează grațios, fără excepție
    assert "commit ok" in msg and "push eșuat" in msg
