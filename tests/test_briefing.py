"""Teste pentru briefingul zilnic (WP-D).

Acoperă: adunarea per-secțiune (joburi noi peste noapte, taskuri programate azi,
buget, vault), degradarea grațioasă a fiecărei secțiuni, intro-ul pe T2 local cu
fallback, randarea (markdown pentru comandă / HTML pentru push) și faptul că
compunerea nu atinge NICIUN tier cloud (zero cost cloud).

Toate rulează pe SQLite in-memory + monkeypatch; niciun apel real de rețea.
"""
import datetime
import json
import sqlite3

import pytest

import orchestrator


# ── Fixturi ───────────────────────────────────────────────────────────────────

@pytest.fixture
def jobs_db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    orchestrator._ensure_jobs_table(conn)
    monkeypatch.setattr(orchestrator, "_db_conn", conn)
    yield conn
    conn.close()


def _insert_job(conn, hash_, profile, title, company, status, score, first_seen):
    conn.execute(
        "INSERT INTO jobs (hash, profile, title, company, location, url, site, "
        "description, score, status, first_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (hash_, profile, title, company, "", "", "linkedin", "", score, status, first_seen),
    )
    conn.commit()


def _fake_httpx_content(monkeypatch, content):
    class _Resp:
        def json(self):
            return {"choices": [{"message": {"content": content}}]}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return _Resp()

    monkeypatch.setattr(orchestrator.httpx, "AsyncClient", lambda *a, **k: _Client())


# ── Joburi noi peste noapte ───────────────────────────────────────────────────

def test_new_jobs_groups_by_profile_recent_only(jobs_db):
    now = datetime.datetime.now()
    recent = (now - datetime.timedelta(hours=2)).isoformat()
    old = (now - datetime.timedelta(hours=48)).isoformat()
    _insert_job(jobs_db, "a", "stefan", "QA Intern", "Acme", orchestrator._JOB_STATUS_SENT, 8, recent)
    _insert_job(jobs_db, "b", "stefan", "Dev Junior", "Globex", orchestrator._JOB_STATUS_SAVED, 7, recent)
    _insert_job(jobs_db, "c", "tata", "PM", "Initech", orchestrator._JOB_STATUS_SENT, 9, recent)
    _insert_job(jobs_db, "d", "stefan", "Vechi", "Old", orchestrator._JOB_STATUS_SENT, 6, old)

    jobs = orchestrator._briefing_new_jobs(since_hours=24)
    assert set(jobs.keys()) == {"stefan", "tata"}
    assert len(jobs["stefan"]) == 2           # 'vechi' e >24h → exclus
    assert jobs["stefan"][0]["score"] == 8    # ordonat desc după scor
    assert len(jobs["tata"]) == 1


def test_new_jobs_excludes_irrelevant_statuses(jobs_db):
    recent = datetime.datetime.now().isoformat()
    _insert_job(jobs_db, "a", "stefan", "Skipped", "X", orchestrator._JOB_STATUS_SKIPPED, 3, recent)
    _insert_job(jobs_db, "b", "stefan", "Ignored", "Y", orchestrator._JOB_STATUS_IGNORED, 2, recent)
    _insert_job(jobs_db, "c", "stefan", "New", "Z", orchestrator._JOB_STATUS_NEW, 0, recent)
    assert orchestrator._briefing_new_jobs() == {}


def test_new_jobs_no_db_degrades(monkeypatch):
    monkeypatch.setattr(orchestrator, "_db_conn", None)
    assert orchestrator._briefing_new_jobs() == {}


# ── Taskuri programate azi ────────────────────────────────────────────────────

def _seed_task(task_id, cron, message, enabled=True):
    import pg_store
    pg_store.execute(
        "INSERT INTO scheduled_tasks (id, cron, message, enabled) VALUES (%s, %s, %s, %s)",
        (task_id, cron, message, enabled))


def test_scheduled_today_matches_daily(pg):
    _seed_task("1", "0 9 * * *", "task zilnic", True)
    _seed_task("2", "30 8 * * *", "dezactivat", False)

    today = orchestrator._briefing_scheduled_today()
    assert len(today) == 1                    # zilnic da, dezactivat nu
    assert today[0]["at"] == "09:00"
    assert today[0]["message"] == "task zilnic"


def test_scheduled_today_skips_other_weekday(pg):
    # Un task care rulează într-o zi a săptămânii care NU e azi → exclus.
    other_dow = (datetime.date.today().weekday() + 2) % 7  # +2 ca să nu prindem azi
    # cron day_of_week: 0=luni în APScheduler from_crontab? Standard cron: 0=duminică.
    _seed_task("1", f"0 10 * * {other_dow}", "săptămânal", True)
    today = orchestrator._briefing_scheduled_today()
    assert today == [] or all(t["message"] != "săptămânal" for t in today)


def test_scheduled_today_no_db_degrades():
    # reset_global_state lasă pg_store neconfigurat → degradare la []
    assert orchestrator._briefing_scheduled_today() == []


def test_scheduled_today_bad_cron_skipped(pg):
    _seed_task("1", "not a cron", "invalid", True)
    assert orchestrator._briefing_scheduled_today() == []


# ── Vault ─────────────────────────────────────────────────────────────────────

def test_vault_today_reads_daily_note(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "VAULT", tmp_path)
    monkeypatch.setattr(orchestrator, "BRIEFING_VAULT_SECTION", True)
    monkeypatch.setattr(orchestrator, "BRIEFING_VAULT_DAILY_DIR", "")
    today = datetime.date.today().isoformat()
    (tmp_path / f"{today}.md").write_text("# Titlu\n\nDe făcut azi: exam.\n", encoding="utf-8")
    out = orchestrator._briefing_vault_today()
    assert out == "De făcut azi: exam."      # header-ul '#' sărit


def test_vault_today_missing_degrades(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "VAULT", tmp_path)
    monkeypatch.setattr(orchestrator, "BRIEFING_VAULT_SECTION", True)
    monkeypatch.setattr(orchestrator, "BRIEFING_VAULT_DAILY_DIR", "")
    assert orchestrator._briefing_vault_today() is None


def test_vault_section_disabled(monkeypatch):
    monkeypatch.setattr(orchestrator, "BRIEFING_VAULT_SECTION", False)
    assert orchestrator._briefing_vault_today() is None


# ── Intro pe T2 local (zero cloud) ────────────────────────────────────────────

async def test_intro_uses_t2_local(monkeypatch):
    """Intro-ul cere modelul T2 (local), nu un tier cloud."""
    seen = {}

    class _Resp:
        def json(self): return {"choices": [{"message": {"content": "Bună dimineața, o zi bună!"}}]}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, **k):
            seen["model"] = json["model"]
            return _Resp()

    monkeypatch.setattr(orchestrator, "BRIEFING_INTRO_LLM", True)
    monkeypatch.setattr(orchestrator.httpx, "AsyncClient", lambda *a, **k: _Client())
    data = {"jobs": {}, "tasks": [], "budget": {"cloud": 0, "max": 20}}
    intro = await orchestrator._briefing_intro(data)
    assert intro == "Bună dimineața, o zi bună!"
    assert seen["model"] == orchestrator.TIER_MODELS[2]     # T2 local


async def test_intro_fallback_on_error(monkeypatch):
    class _Boom:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): raise RuntimeError("ollama down")
    monkeypatch.setattr(orchestrator, "BRIEFING_INTRO_LLM", True)
    monkeypatch.setattr(orchestrator.httpx, "AsyncClient", lambda *a, **k: _Boom())
    intro = await orchestrator._briefing_intro({"budget": {"cloud": 0, "max": 20}})
    assert intro == orchestrator._BRIEFING_FALLBACK_INTRO


async def test_intro_llm_disabled_no_call(monkeypatch):
    """intro_llm=false → intro static, fără NICIUN apel de model."""
    def _boom(*a, **k):
        raise AssertionError("nu ar trebui apelat httpx")
    monkeypatch.setattr(orchestrator, "BRIEFING_INTRO_LLM", False)
    monkeypatch.setattr(orchestrator.httpx, "AsyncClient", _boom)
    intro = await orchestrator._briefing_intro({"budget": {"cloud": 0, "max": 20}})
    assert intro == orchestrator._BRIEFING_FALLBACK_INTRO


# ── Randare ───────────────────────────────────────────────────────────────────

def _sample_data():
    return {
        "date": datetime.date(2026, 7, 6),
        "jobs": {"stefan": [{"title": "QA Intern", "company": "Acme", "score": 8, "status": "sent"}]},
        "tasks": [{"at": "09:00", "message": "recap zilnic"}],
        "budget": {"total": 10, "cloud": 4, "max": 20},
        "missions": None,
        "vault": "exam la 14:00",
    }


def test_render_markdown_has_all_sections():
    text = orchestrator._briefing_render(_sample_data(), "Salut!", html=False)
    assert "**Briefing — 06.07.2026**" in text
    assert "QA Intern" in text and "Acme" in text
    assert "4/20" in text                     # buget
    assert "recap zilnic" in text             # task programat
    assert "exam la 14:00" in text            # vault
    assert "<b>" not in text                  # markdown, nu HTML


def test_render_html_escapes_dynamic_text():
    data = _sample_data()
    data["jobs"] = {"stefan": [{"title": "R&D <Lead>", "company": "A&B", "score": 5, "status": "sent"}]}
    text = orchestrator._briefing_render(data, "bună", html=True)
    assert "<b>Briefing" in text              # HTML bold pt push
    assert "R&amp;D &lt;Lead&gt;" in text     # caracterele periculoase escape-uite
    assert "A&amp;B" in text


def test_render_degrades_missing_sections():
    data = {
        "date": datetime.date(2026, 7, 6),
        "jobs": {}, "tasks": [], "budget": {"cloud": 0, "max": 20},
        "missions": None, "vault": None,
    }
    text = orchestrator._briefing_render(data, "Bună dimineața.", html=False)
    assert "Briefing" in text and "Bună dimineața." in text
    assert "Buget" in text                    # bugetul e mereu prezent
    assert "Joburi noi" not in text           # secțiunile goale sunt omise
    assert "Programate azi" not in text
    assert "vault" not in text.lower()


# ── Compunere end-to-end (zero cloud) ─────────────────────────────────────────

async def test_compose_briefing_zero_cloud(monkeypatch, jobs_db):
    """`_compose_briefing` nu apelează NICIODATĂ un tier cloud."""
    recent = datetime.datetime.now().isoformat()
    _insert_job(jobs_db, "a", "stefan", "QA Intern", "Acme", orchestrator._JOB_STATUS_SENT, 8, recent)
    monkeypatch.setattr(orchestrator, "BRIEFING_VAULT_SECTION", False)
    _fake_httpx_content(monkeypatch, "Bună dimineața!")

    # santinelă: orice rutare cloud aruncă
    async def _no_cloud(*a, **k):
        raise AssertionError("compunerea nu are voie să atingă cloud")
    monkeypatch.setattr(orchestrator, "_route_claude_autonomous", _no_cloud, raising=False)

    text = await orchestrator._compose_briefing(html=True)
    assert "QA Intern" in text
    assert "Bună dimineața!" in text


async def test_handle_briefing_command_returns_sse(monkeypatch, jobs_db):
    monkeypatch.setattr(orchestrator, "BRIEFING_VAULT_SECTION", False)
    _fake_httpx_content(monkeypatch, "Salut!")
    resp = await orchestrator._handle_briefing_command()
    body = "".join([chunk async for chunk in resp.body_iterator])
    assert "Briefing" in body
    assert "[DONE]" in body


async def test_send_briefing_no_gateway_noop(monkeypatch):
    monkeypatch.setattr(orchestrator, "_tg_gateway", None)
    monkeypatch.setattr(orchestrator, "BRIEFING_VAULT_SECTION", False)
    _fake_httpx_content(monkeypatch, "Salut!")
    # nu trebuie să arunce, doar să logheze
    await orchestrator._send_briefing()


async def test_send_briefing_pushes_to_telegram(monkeypatch):
    sent = {}

    class _Gw:
        async def send(self, text, reply_markup=None):
            sent["text"] = text

    monkeypatch.setattr(orchestrator, "_tg_gateway", _Gw())
    monkeypatch.setattr(orchestrator, "BRIEFING_VAULT_SECTION", False)
    monkeypatch.setattr(orchestrator, "_db_conn", None)
    _fake_httpx_content(monkeypatch, "Bună dimineața!")
    await orchestrator._send_briefing()
    assert "Briefing" in sent["text"]
    assert "<b>" in sent["text"]              # push-ul e HTML
