"""Teste WP12 — telecomandă.

Slice 1: `!mission new`/`revise` + carduri Telegram + wiring briefing. Ciclul de draft e
testat cu `_agent_complete` mock-uit (fără apel LLM real) și `_mission_launch` mock-uit.
Slice 2: branch+push per WP (git pe un repo temporar, NU cel real) + watchdog (job_runs +
recuperarea joburilor întrerupte).
"""
import asyncio
import subprocess
import sqlite3

import pytest

import orchestrator
import pg_store
import telegram_gateway


_DRAFT_MD = """# Mission: Adaugă un endpoint de health

## WP1 — endpoint /ping
- adaugă ruta
### Acceptare
- `true`

## WP2 — test
- scrie testul
### Acceptare
- `true`
"""

_DRAFT_MD_REVISED = """# Mission: Adaugă un endpoint de health (revizuit)

## WP1 — endpoint /ping cu auth
- adaugă ruta + auth
### Acceptare
- `true`
"""


@pytest.fixture
def mdb(pg, monkeypatch, tmp_path):
    """missions/mission_wps → PG de test (WP-PG); LLM/launch mock-uite."""
    monkeypatch.setattr(orchestrator, "_active_mission_id", None)
    monkeypatch.setattr(orchestrator, "_tg_gateway", None)
    monkeypatch.setattr(orchestrator, "MISSIONS_DIR", tmp_path / "missions")

    launched = []
    monkeypatch.setattr(orchestrator, "_mission_launch", lambda mid: launched.append(mid))
    orchestrator._launched = launched

    # _agent_complete întoarce un mission.md canonic (fără apel LLM real).
    async def _fake_complete(prompt, *, model, system=None):
        return _DRAFT_MD_REVISED if "Revizuiește" in prompt else _DRAFT_MD
    monkeypatch.setattr(orchestrator, "_agent_complete", _fake_complete)
    yield orchestrator


# ── Helpers puri ──────────────────────────────────────────────────────────────

def test_extract_mission_md_strips_fence():
    fenced = "Iată planul:\n```markdown\n# Mission: X\n\n## WP1\n- a\n```\ngata"
    out = orchestrator._extract_mission_md(fenced)
    assert out.startswith("# Mission: X")
    assert "```" not in out


def test_extract_mission_md_passthrough():
    raw = "# Mission: X\n\n## WP1\n- a"
    assert orchestrator._extract_mission_md(raw) == raw


def test_slugify_kebab():
    slug = orchestrator._mission_slugify("Adaugă un Endpoint de Health!!")
    assert slug.split("-")[0] == "adaug"  # diacriticele/nealfanumericele devin liniuțe
    assert " " not in slug and slug.lower() == slug


# ── _mission_new ──────────────────────────────────────────────────────────────

async def test_mission_new_creates_draft(mdb, tmp_path):
    orch = mdb
    mid, err = await orch._mission_new("adaugă endpoint de health")
    assert err is None and mid
    row = orch._mission_row(mid)
    assert row["status"] == "draft"
    assert row["title"] == "Adaugă un endpoint de health"
    wps = orch._mission_wps(mid)
    assert [w["status"] for w in wps] == ["pending", "pending"]
    # fișierul mission.md a fost scris sub MISSIONS_DIR
    from pathlib import Path
    assert Path(row["path"]).is_file()
    assert orch.MISSIONS_DIR in Path(row["path"]).parents
    # NU s-a lansat nimic (e doar draft)
    assert orch._launched == []


async def test_mission_new_rejects_empty_draft(mdb, monkeypatch):
    orch = mdb

    async def _empty(prompt, *, model, system=None):
        return "doar text, fără pachete de lucru"
    monkeypatch.setattr(orch, "_agent_complete", _empty)
    mid, err = await orch._mission_new("ceva")
    assert mid is None and "pachete de lucru" in err


# ── _mission_revise ───────────────────────────────────────────────────────────

async def test_mission_revise_updates_draft(mdb):
    orch = mdb
    mid, _ = await orch._mission_new("adaugă endpoint de health")
    assert len(orch._mission_wps(mid)) == 2
    assert orch._latest_draft_id() == mid
    rid, err = await orch._mission_revise(mid, "adaugă auth")
    assert err is None and rid == mid
    row = orch._mission_row(mid)
    assert "revizuit" in row["title"]
    assert len(orch._mission_wps(mid)) == 1   # WP-urile au fost rescrise


async def test_mission_revise_refuses_non_draft(mdb):
    orch = mdb
    mid, _ = await orch._mission_new("x")
    orch._mission_update(mid, status="running")
    rid, err = await orch._mission_revise(mid, "schimbă")
    assert rid is None and "draft" in err


# ── start / discard ───────────────────────────────────────────────────────────

async def test_draft_start(mdb):
    orch = mdb
    mid, _ = await orch._mission_new("x")
    ok, info = orch._mission_draft_start(mid)
    assert ok is True
    assert orch._mission_row(mid)["status"] == "running"
    assert orch._launched == [mid]


async def test_draft_start_refuses_when_active(mdb, monkeypatch):
    orch = mdb
    mid, _ = await orch._mission_new("x")
    monkeypatch.setattr(orch, "_active_mission_id", "other")
    ok, info = orch._mission_draft_start(mid)
    assert ok is False and "rulează deja" in info
    assert orch._mission_row(mid)["status"] == "draft"   # neschimbat
    assert orch._launched == []


async def test_draft_discard_deletes(mdb):
    orch = mdb
    from pathlib import Path
    mid, _ = await orch._mission_new("x")
    path = orch._mission_row(mid)["path"]
    ok, info = orch._mission_draft_discard(mid)
    assert ok is True
    assert orch._mission_row(mid) is None
    assert orch._mission_wps(mid) == []
    assert not Path(path).exists()


# ── _briefing_missions ────────────────────────────────────────────────────────

def test_briefing_missions_none_when_empty(mdb):
    assert mdb._briefing_missions() is None


async def test_briefing_missions_lists_active_and_draft(mdb):
    orch = mdb
    mid, _ = await orch._mission_new("una")   # rămâne draft
    mid2, _ = await orch._mission_new("două")
    orch._mission_update(mid2, status="running")
    out = orch._briefing_missions()
    assert out is not None
    statuses = {m["status"] for m in out}
    assert "draft" in statuses and "running" in statuses
    assert all("name" in m and "status" in m for m in out)


# ── Gateway: cardul de schiță ─────────────────────────────────────────────────

def _make_gateway():
    return telegram_gateway.TelegramGateway(
        bot_token="t", chat_id="123", orchestrator_base_url="http://x", api_token="k")


async def test_gateway_draft_card_keyboard():
    gw = _make_gateway()
    sent = {}

    async def _fake_send(text, reply_markup=None):
        sent["text"] = text
        sent["kb"] = reply_markup
    gw.send = _fake_send
    await gw.send_mission_draft("m123", "Titlu misiune", ["WP unu", "WP doi"])
    assert "Titlu misiune" in sent["text"]
    buttons = sent["kb"]["inline_keyboard"][0]
    actions = [b["callback_data"] for b in buttons]
    assert actions == ["missiondraft:start:m123", "missiondraft:revise:m123",
                       "missiondraft:discard:m123"]


async def test_gateway_revise_capture_rewrites_message():
    gw = _make_gateway()
    forwarded = []

    async def _fake_forward(text):
        forwarded.append(text)
    gw._forward_to_orchestrator = _fake_forward

    # ✏️ a armat captura
    gw._pending_revise = True
    await gw._handle_message("adaugă și logging")
    assert forwarded == ["!mission revise adaugă și logging"]
    assert gw._pending_revise is False   # consumat


async def test_gateway_revise_capture_cancelled_by_command():
    gw = _make_gateway()
    forwarded = []

    async def _fake_forward(text):
        forwarded.append(text)
    gw._forward_to_orchestrator = _fake_forward

    gw._pending_revise = True
    await gw._handle_message("!status")   # o comandă anulează așteptarea
    assert gw._pending_revise is False
    assert forwarded == ["!status"]       # trimisă ca atare, nu ca revizie


# ══ Slice 2: branch + push per WP ═════════════════════════════════════════════

def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)


@pytest.fixture
def gitrepo(monkeypatch, tmp_path):
    """Repo git temporar cu un commit inițial — PROJECT_ROOT redirecționat aici, ca testele
    de git ale misiunii să NU atingă repo-ul real."""
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
    monkeypatch.setattr(orchestrator, "MISSION_GIT_BRANCH", True)
    monkeypatch.setattr(orchestrator, "MISSION_GIT_PUSH", False)
    monkeypatch.setattr(orchestrator, "MISSION_GIT_REMOTE", "origin")
    yield repo


def test_branch_name_sanitizes():
    assert orchestrator._mission_branch_name("Adaugă X!!") == "mission/Adaug-X"
    assert orchestrator._mission_branch_name("") == "mission/misiune"


def test_github_slug_and_compare(gitrepo):
    _git(gitrepo, "remote", "add", "origin", "https://github.com/st3fansrb/kage.ai.git")
    assert orchestrator._github_repo_slug() == "st3fansrb/kage.ai"
    url = orchestrator._github_compare_url("mission/x")
    assert url == "https://github.com/st3fansrb/kage.ai/compare/mission/x?expand=1"


def test_github_slug_none_without_github_remote(gitrepo):
    _git(gitrepo, "remote", "add", "origin", "/tmp/some/local/path.git")
    assert orchestrator._github_repo_slug() is None
    assert orchestrator._github_compare_url("mission/x") is None


def test_ensure_branch_creates_and_switches(gitrepo):
    branch = orchestrator._mission_git_ensure_branch("misiune-test")
    assert branch == "mission/misiune-test"
    head = _git(gitrepo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert head == "mission/misiune-test"
    # a doua oară = no-op (deja pe branch)
    assert orchestrator._mission_git_ensure_branch("misiune-test") == "mission/misiune-test"


def test_ensure_branch_disabled(gitrepo, monkeypatch):
    monkeypatch.setattr(orchestrator, "MISSION_GIT_BRANCH", False)
    assert orchestrator._mission_git_ensure_branch("x") is None


def test_mark_and_commit_commits_full_diff(gitrepo):
    orchestrator._mission_git_ensure_branch("mm")
    md = gitrepo / "mission.md"
    md.write_text("# Mission: X\n\n## WP1 — a\n- pas\n### Acceptare\n- `true`\n", encoding="utf-8")
    (gitrepo / "cod_nou.py").write_text("print('agent')\n", encoding="utf-8")  # „codul agentului"
    info = orchestrator._mission_mark_and_commit(str(md), 0, "WP1 — a")
    assert info and info["branch"] == "mission/mm"
    assert info["pushed"] is False   # push dezactivat
    # commit-ul conține ATÂT mission.md marcat ✅, CÂT ȘI fișierul nou (întregul diff)
    files = _git(gitrepo, "show", "--name-only", "--pretty=format:", "HEAD").stdout
    assert "mission.md" in files and "cod_nou.py" in files
    assert "✅" in md.read_text(encoding="utf-8")


def test_push_to_bare_remote(gitrepo, monkeypatch, tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(gitrepo, "remote", "add", "origin", str(bare))
    monkeypatch.setattr(orchestrator, "MISSION_GIT_PUSH", True)
    orchestrator._mission_git_ensure_branch("pushtest")
    md = gitrepo / "mission.md"
    md.write_text("# Mission: X\n\n## WP1\n- a\n### Acceptare\n- `true`\n", encoding="utf-8")
    info = orchestrator._mission_mark_and_commit(str(md), 0, "WP1")
    assert info["pushed"] is True
    # branch-ul a ajuns pe remote
    refs = subprocess.run(["git", "branch"], cwd=str(bare), capture_output=True, text=True).stdout
    assert "mission/pushtest" in refs


# ══ Slice 2: watchdog (job_runs + recuperare) ═════════════════════════════════

@pytest.fixture
def jrdb(pg, monkeypatch):
    """job_runs → PG de test (WP-PG)."""
    monkeypatch.setattr(orchestrator, "_tg_gateway", None)
    yield orchestrator


def test_job_run_begin_end(jrdb):
    orch = jrdb
    rid = orch._job_run_begin("__x__")
    assert rid is not None
    row = pg_store.fetchone(
        "SELECT finished_at, status FROM job_runs WHERE id=%s", (rid,))
    assert row[0] is None   # încă deschis
    orch._job_run_end(rid, "done")
    row = pg_store.fetchone(
        "SELECT finished_at, status FROM job_runs WHERE id=%s", (rid,))
    assert row[0] is not None and row[1] == "done"


async def test_tracked_job_records_finish(jrdb):
    orch = jrdb
    ran = []

    async def _work():
        ran.append(1)
    await orch._tracked_job("__t__", _work())
    assert ran == [1]
    row = pg_store.fetchone(
        "SELECT status, finished_at FROM job_runs WHERE job_id='__t__'")
    assert row[0] == "done" and row[1] is not None


async def test_tracked_job_records_failure(jrdb):
    orch = jrdb

    async def _boom():
        raise ValueError("x")
    with pytest.raises(ValueError):
        await orch._tracked_job("__t__", _boom())
    row = pg_store.fetchone(
        "SELECT status FROM job_runs WHERE job_id='__t__'")
    assert row[0] == "failed"


async def test_recover_interrupted_retriggers(jrdb, monkeypatch):
    orch = jrdb
    # simulează un scan întrerupt: rând deschis (finished_at NULL)
    pg_store.execute(
        "INSERT INTO job_runs (job_id, started_at) VALUES ('__job_scan__', '2026-07-09T19:00:00')")
    fired = []

    async def _fake_scan():
        fired.append(1)
    monkeypatch.setattr(orch, "_recoverable_jobs", lambda: {"__job_scan__": _fake_scan})
    notes = []

    async def _note(t):
        notes.append(t)
    monkeypatch.setattr(orch, "_mission_notify", _note)

    orch._recover_interrupted_jobs()
    await asyncio.sleep(0)   # lasă task-urile create să ruleze
    await asyncio.sleep(0)

    # rândul întrerupt e marcat, jobul re-declanșat, alertă trimisă
    row = pg_store.fetchone(
        "SELECT status, finished_at FROM job_runs WHERE started_at='2026-07-09T19:00:00'")
    assert row[0] == "interrupted" and row[1] is not None
    assert fired == [1]
    assert notes and "întrerupt" in notes[0]


def test_recover_interrupted_noop_when_clean(jrdb):
    # niciun rând deschis → nimic de făcut, fără excepție
    jrdb._recover_interrupted_jobs()


def test_recoverable_maps_to_raw_coroutine():
    # NU wrapper-ul tracked (ar dubla urmărirea) — vezi _recover_interrupted_jobs.
    assert orchestrator._recoverable_jobs()["__job_scan__"] is orchestrator._job_scan_all
