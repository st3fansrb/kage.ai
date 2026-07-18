"""Teste WP-SD — misiunile care țintesc repo-ul Kage însuși rulează izolat într-un
git worktree separat, nu pe checkout-ul viu (PROJECT_ROOT). Acoperă: funcțiile pure de
worktree (creare/reutilizare/cleanup), seed-ul mission.md, git bookkeeping izolat, și
bucla completă `_mission_run` — criteriul de acceptare central: branch-ul PROJECT_ROOT
rămâne NESCHIMBAT pe toată durata unei misiuni pe Kage.
"""
import subprocess
import sqlite3
from pathlib import Path

import pytest

import orchestrator


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)


@pytest.fixture
def gitrepo(monkeypatch, tmp_path):
    """Repo git temporar cu un commit inițial — PROJECT_ROOT redirecționat aici, ca
    testele WP-SD să NU atingă repo-ul real."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "checkout", "-q", "-b", "dev")
    (repo / "seed.txt").write_text("seed", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    monkeypatch.setattr(orchestrator, "PROJECT_ROOT", repo)
    monkeypatch.setattr(orchestrator, "KAGE_WORKTREES_DIR", tmp_path / "worktrees")
    monkeypatch.setattr(orchestrator, "MISSION_GIT_BRANCH", True)
    monkeypatch.setattr(orchestrator, "MISSION_GIT_PUSH", False)
    # Testele astea nu verifică confinement-ul (separat, în test_risk.py) — dezactivat
    # ca să nu depindă de ce e configurat în allowed_task_roots-ul REAL al mașinii.
    monkeypatch.setattr(orchestrator, "ALLOWED_TASK_ROOTS", [])
    yield repo


# ── funcții pure ──────────────────────────────────────────────────────────────

def test_targets_project_root_true_for_exact_match(gitrepo):
    assert orchestrator._mission_targets_project_root(str(gitrepo)) is True


def test_targets_project_root_true_for_relative_variant(gitrepo):
    assert orchestrator._mission_targets_project_root(str(gitrepo) + "/.") is True


def test_targets_project_root_false_for_other_path(gitrepo, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert orchestrator._mission_targets_project_root(str(other)) is False


def test_worktree_path_deterministic_from_slug(gitrepo):
    p1 = orchestrator._mission_worktree_path("my-slug")
    p2 = orchestrator._mission_worktree_path("my-slug")
    assert p1 == p2 == orchestrator.KAGE_WORKTREES_DIR / "my-slug"


# ── _mission_ensure_worktree ────────────────────────────────────────────────────

def test_ensure_worktree_creates_new_branch_without_touching_live_checkout(gitrepo):
    path = orchestrator._mission_ensure_worktree("wpsd-test")
    assert path is not None and path.is_dir()
    assert _git(path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "mission/wpsd-test"
    # checkout-ul viu (PROJECT_ROOT) NU a fost atins — rămâne pe branch-ul lui
    assert _git(gitrepo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "dev"


def test_ensure_worktree_reuses_existing_on_resume(gitrepo):
    path1 = orchestrator._mission_ensure_worktree("wpsd-reuse")
    (path1 / "marker.txt").write_text("still here", encoding="utf-8")
    path2 = orchestrator._mission_ensure_worktree("wpsd-reuse")
    assert path1 == path2
    assert (path2 / "marker.txt").exists()  # nu a fost recreat — capcana din spec


def test_ensure_worktree_none_without_git_repo(tmp_path, monkeypatch):
    fake_root = tmp_path / "not-a-repo"
    fake_root.mkdir()
    monkeypatch.setattr(orchestrator, "PROJECT_ROOT", fake_root)
    monkeypatch.setattr(orchestrator, "KAGE_WORKTREES_DIR", tmp_path / "worktrees")
    assert orchestrator._mission_ensure_worktree("x") is None


def test_ensure_worktree_refuses_wrong_branch(gitrepo):
    """High (review Codex 15.07): un director existent pe ALT branch e refuzat, nu
    refolosit — altfel commit-urile misiunii ar ajunge pe branch-ul greșit."""
    path = orchestrator._mission_ensure_worktree("wpsd-mismatch")
    assert path is not None
    _git(path, "checkout", "-q", "-b", "alt-branch")     # intervenție manuală
    assert orchestrator._mission_ensure_worktree("wpsd-mismatch") is None


def test_ensure_worktree_refuses_non_worktree_dir(gitrepo):
    """Un director rezidual care nu e worktree git valid e refuzat (rev-parse pică)."""
    path = orchestrator._mission_worktree_path("wpsd-junk")
    path.mkdir(parents=True)
    (path / "junk.txt").write_text("nu e worktree", encoding="utf-8")
    assert orchestrator._mission_ensure_worktree("wpsd-junk") is None


# ── _mission_seed_worktree_path ─────────────────────────────────────────────────

def test_seed_worktree_copies_missing_mission_md(gitrepo):
    worktree = orchestrator._mission_ensure_worktree("seed-test")
    src = gitrepo / "missions" / "seed-test" / "mission.md"
    src.parent.mkdir(parents=True)
    body = "# Mission: X\n\n## WP1\n- a\n"
    src.write_text(body, encoding="utf-8")
    effective = orchestrator._mission_seed_worktree_path(worktree, str(src))
    assert Path(effective) == worktree / "missions" / "seed-test" / "mission.md"
    assert Path(effective).read_text(encoding="utf-8") == body
    # originalul din PROJECT_ROOT e curățat (nu rămâne orfan, necomis)
    assert not src.exists()
    assert not src.parent.exists()


def test_seed_worktree_does_not_overwrite_existing_progress(gitrepo):
    worktree = orchestrator._mission_ensure_worktree("seed-keep")
    src = gitrepo / "missions" / "seed-keep" / "mission.md"
    src.parent.mkdir(parents=True)
    src.write_text("# Mission: X (stale, fără progres)\n", encoding="utf-8")
    dst = worktree / "missions" / "seed-keep" / "mission.md"
    dst.parent.mkdir(parents=True)
    dst.write_text("# Mission: X\n\n## WP1 ✅\n", encoding="utf-8")  # progres deja făcut
    effective = orchestrator._mission_seed_worktree_path(worktree, str(src))
    assert "✅" in Path(effective).read_text(encoding="utf-8")


# ── _mission_cleanup_worktree ────────────────────────────────────────────────────

def test_cleanup_removes_when_not_keep(gitrepo):
    path = orchestrator._mission_ensure_worktree("cleanup-remove")
    assert path.exists()
    orchestrator._mission_cleanup_worktree("cleanup-remove", keep=False)
    assert not path.exists()


def test_cleanup_keeps_on_failure_for_autopsy(gitrepo):
    path = orchestrator._mission_ensure_worktree("cleanup-keep")
    assert path.exists()
    orchestrator._mission_cleanup_worktree("cleanup-keep", keep=True)
    assert path.exists()


# ── git bookkeeping izolat (_mission_git cu cwd override) ───────────────────────

def test_mark_and_commit_lands_in_worktree_not_project_root(gitrepo):
    worktree = orchestrator._mission_ensure_worktree("gitcwd-test")
    md = worktree / "mission.md"
    md.write_text("# Mission: X\n\n## WP1 — a\n- pas\n### Acceptare\n- `true`\n", encoding="utf-8")
    (worktree / "cod_nou.py").write_text("print('agent')\n", encoding="utf-8")
    info = orchestrator._mission_mark_and_commit(str(md), 0, "WP1 — a", git_cwd=worktree)
    assert info and info["branch"] == "mission/gitcwd-test"
    files = _git(worktree, "show", "--name-only", "--pretty=format:", "HEAD").stdout
    assert "mission.md" in files and "cod_nou.py" in files
    # checkout-ul viu rămâne curat: niciun commit nou, branch neschimbat
    assert _git(gitrepo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "dev"
    assert _git(gitrepo, "status", "--porcelain").stdout.strip() == ""


# ══ Bucla completă _mission_run — criteriul de acceptare central ═════════════════

class _FakeRunner:
    """AgentRunner fals: fiecare apel `run` livrează evenimentele următoare din listă."""
    def __init__(self, events_per_call):
        self.active_clients = set()
        self.calls = []
        self._events = events_per_call

    async def run(self, prompt, **kw):
        i = len(self.calls)
        self.calls.append((prompt, kw))
        for e in self._events[min(i, len(self._events) - 1)]:
            yield e

    async def stop_all(self):
        return 0


def _result_ev(sid="sdk-s"):
    return {"type": "result", "session_id": sid, "cost_usd": 0.01,
            "duration_ms": 5, "text": "gata", "is_error": False}


_ONE_WP = """# Mission: Kage lucrează la Kage

## WP1 — schimbare mică
- fă ceva pe repo-ul propriu
### Acceptare
- `true`
"""


@pytest.fixture
def mission_env(pg, gitrepo, monkeypatch):
    """runs/missions → PG de test; messages + agent_sessions → SQLite in-memory.
    No-op-uri pe caffeinate/telegram, PESTE `gitrepo` (git real).
    Spre deosebire de fixture-ul din test_mission_orchestration.py, AICI
    `_mission_mark_and_commit`/`_mission_git_ensure_branch` NU sunt mock-uite — trebuie
    să ruleze real, ca să verificăm izolarea git efectiv."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("""CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT DEFAULT 'default', role TEXT, content TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    orchestrator._ensure_agent_sessions_table(conn)
    conn.commit()
    monkeypatch.setattr(orchestrator, "_db_conn", conn)
    monkeypatch.setattr(orchestrator, "_active_mission_id", None)
    monkeypatch.setattr(orchestrator, "_mission_stop", {})
    monkeypatch.setattr(orchestrator, "pending_mission_q", {})
    monkeypatch.setattr(orchestrator, "mission_answers", {})
    monkeypatch.setattr(orchestrator, "_tg_gateway", None)
    monkeypatch.setattr(orchestrator, "_mission_caffeinate_start", lambda: None)
    monkeypatch.setattr(orchestrator, "_mission_caffeinate_stop", lambda: None)
    monkeypatch.setattr(orchestrator, "MISSIONS_DIR", gitrepo / "missions")

    async def _fake_decide(msg):
        return (5, False, 1.0, "test")
    monkeypatch.setattr(orchestrator, "decide_tier", _fake_decide)
    yield orchestrator
    conn.close()


def _write_mission(gitrepo, slug, body):
    d = gitrepo / "missions" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "mission.md").write_text(body, encoding="utf-8")
    return slug


@pytest.mark.asyncio
async def test_kage_self_mission_isolates_in_worktree_and_never_touches_live_branch(
    mission_env, gitrepo, monkeypatch,
):
    orch = mission_env
    slug = _write_mission(gitrepo, "self-dev", _ONE_WP)
    # cwd=None → _mission_create cade pe PROJECT_ROOT (cazul „țintește Kage însuși").
    mid, err = orch._mission_create(slug, cwd=None)
    assert err is None and mid
    monkeypatch.setattr(orch, "_agent_runner", _FakeRunner([[_result_ev()]]))

    live_head_before = _git(gitrepo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    await orch._mission_run(mid)
    live_head_after = _git(gitrepo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    # ── criteriul de acceptare central ──
    assert live_head_before == live_head_after == "dev"
    assert _git(gitrepo, "status", "--porcelain").stdout.strip() == ""

    # misiunea a terminat cu succes, prin worktree
    assert orch._mission_row(mid)["status"] == "done"
    worktree_cwd = orch._agent_runner.calls[0][1]["cwd"]
    assert worktree_cwd == str(orch._mission_worktree_path(slug))
    assert worktree_cwd != str(gitrepo)

    # commit-ul a ajuns pe branch-ul misiunii — vizibil din PROJECT_ROOT, fiindcă
    # worktree-ul partajează `.git` (istoricul supraviețuiește chiar și după cleanup).
    log = _git(gitrepo, "log", "--oneline", "mission/self-dev").stdout
    assert "WP1" in log

    # worktree-ul (directorul de lucru) curățat la succes — spec: păstrat doar la eșec.
    assert not Path(worktree_cwd).exists()


@pytest.mark.asyncio
async def test_kage_self_mission_fails_closed_without_worktree(mission_env, gitrepo, monkeypatch):
    """Critical (review Codex 15.07): dacă worktree-ul nu poate fi creat/refolosit,
    misiunea pe repo-ul Kage se OPREȘTE (paused + alertă) — nu cade pe checkout-ul viu."""
    orch = mission_env
    slug = _write_mission(gitrepo, "self-noiso", _ONE_WP)
    mid, err = orch._mission_create(slug, cwd=None)
    assert err is None and mid
    runner = _FakeRunner([[_result_ev()]])
    monkeypatch.setattr(orch, "_agent_runner", runner)
    monkeypatch.setattr(orch, "_mission_ensure_worktree", lambda s: None)
    ensured = []
    monkeypatch.setattr(orch, "_mission_git_ensure_branch",
                        lambda s: ensured.append(s))
    notes = []

    async def _note(t):
        notes.append(t)
    monkeypatch.setattr(orch, "_mission_notify", _note)

    live_head_before = _git(gitrepo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    await orch._mission_run(mid)

    assert orch._mission_row(mid)["status"] == "paused"        # fail-closed, reluabil
    assert runner.calls == []                                   # agentul NU a pornit
    assert ensured == []                                        # checkout-ul viu neatins
    assert _git(gitrepo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == live_head_before
    assert orch._active_mission_id is None                      # slotul de misiune eliberat
    assert notes and "fail-closed" in notes[0]


@pytest.mark.asyncio
async def test_kage_self_mission_keeps_worktree_on_failure(mission_env, gitrepo, monkeypatch):
    orch = mission_env
    body = "# Mission: X\n\n## WP1 — pică\n- x\n### Acceptare\n- `false`\n"
    slug = _write_mission(gitrepo, "self-fail", body)
    mid, _ = orch._mission_create(slug, cwd=None)
    monkeypatch.setattr(orch, "_agent_runner", _FakeRunner([[_result_ev()]]))

    async def _abort(*a, **k):
        return "abort"
    monkeypatch.setattr(orch, "_mission_ask", _abort)

    await orch._mission_run(mid)

    assert orch._mission_row(mid)["status"] == "failed"
    wt = orch._mission_worktree_path(slug)
    assert wt.exists()   # păstrat pentru autopsie, NU curățat
    # tot neatins pe checkout-ul viu
    assert _git(gitrepo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "dev"


@pytest.mark.asyncio
async def test_external_repo_mission_unaffected_by_wpsd(mission_env, gitrepo, tmp_path, monkeypatch):
    """O misiune pe alt repo (nu Kage) trebuie să meargă EXACT ca înainte de WP-SD —
    fără worktree, pe cwd-ul ei propriu."""
    other = tmp_path / "other-project"
    other.mkdir()
    orch = mission_env
    slug = _write_mission(gitrepo, "external", _ONE_WP)
    mid, _ = orch._mission_create(slug, cwd=str(other))
    monkeypatch.setattr(orch, "_agent_runner", _FakeRunner([[_result_ev()]]))
    monkeypatch.setattr(orch, "_mission_git_ensure_branch", lambda s: None)  # ca înainte de WP-SD

    await orch._mission_run(mid)

    assert orch._agent_runner.calls[0][1]["cwd"] == str(other)
    assert not orch._mission_worktree_path(slug).exists()  # niciun worktree creat
