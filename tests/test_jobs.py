"""Teste pentru job hunter-ul multi-profil (WP-J).

Acoperă: hash-ul de dedup, tabelul `jobs`, pre-filtrul pe T2 local (mock httpx),
pipeline-ul scan→dedup→prefiltru→selecție, acțiunile 🔖/🗑 și ✍️ (career-ops).
Toate rulează pe un SQLite in-memory; niciun apel real de rețea/subprocess.
"""
import sqlite3

import pytest

import orchestrator


# ── Fixturi ───────────────────────────────────────────────────────────────────

@pytest.fixture
def jobs_db(monkeypatch):
    """SQLite in-memory cu tabelul `jobs`, injectat ca _db_conn."""
    conn = sqlite3.connect(":memory:")
    orchestrator._ensure_jobs_table(conn)
    monkeypatch.setattr(orchestrator, "_db_conn", conn)
    yield conn
    conn.close()


_PROFILE = {
    "id": "stefan",
    "label": "👔 Stefan",
    "workspace": "~/career-ops/stefan",
    "criteria": "QA intern, entry-level",
}


def _fake_httpx_content(monkeypatch, content):
    """Înlocuiește httpx.AsyncClient cu un client care întoarce `content` la orice POST."""
    class _Resp:
        def json(self):
            return {"choices": [{"message": {"content": content}}]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(orchestrator.httpx, "AsyncClient", lambda *a, **k: _Client())


# ── _job_hash ─────────────────────────────────────────────────────────────────

def test_job_hash_deterministic_and_normalized():
    a = orchestrator._job_hash("QA Intern", "Acme")
    b = orchestrator._job_hash("  qa intern ", "  ACME ")
    assert a == b                      # normalizare case + whitespace
    assert len(a) == 16                # intră în callback_data Telegram
    assert a != orchestrator._job_hash("QA Intern", "Globex")


# ── Pre-filtru (T2 local) ─────────────────────────────────────────────────────

async def test_prefilter_parses_score(monkeypatch):
    _fake_httpx_content(monkeypatch, "8")
    score = await orchestrator._prefilter_score(_PROFILE, {"title": "QA", "company": "X", "description": "test"})
    assert score == 8


async def test_prefilter_clamps_and_extracts(monkeypatch):
    _fake_httpx_content(monkeypatch, "Scor: 15/10")  # extrage primul număr, apoi clamp
    assert await orchestrator._prefilter_score(_PROFILE, {}) == 10


async def test_prefilter_zero_on_garbage(monkeypatch):
    _fake_httpx_content(monkeypatch, "nu știu")
    assert await orchestrator._prefilter_score(_PROFILE, {}) == 0


async def test_prefilter_zero_on_error(monkeypatch):
    class _Boom:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): raise RuntimeError("down")
    monkeypatch.setattr(orchestrator.httpx, "AsyncClient", lambda *a, **k: _Boom())
    assert await orchestrator._prefilter_score(_PROFILE, {}) == 0


# ── Keyword screen (fără LLM) ─────────────────────────────────────────────────

def test_keyword_screen_no_gate_passes():
    # Fără include/exclude → nimic tăiat.
    assert orchestrator._keyword_screen({}, {"title": "X", "description": "y"}) is None


def test_keyword_screen_excludes_senior():
    prof = {"exclude_keywords": ["senior", "5+ years"]}
    cut = orchestrator._keyword_screen(prof, {"title": "Senior AI Engineer", "description": "..."})
    assert cut and cut.startswith("exclude:")
    # word-boundary: „seniority" NU declanșează „senior"
    assert orchestrator._keyword_screen(prof, {"title": "Growth", "description": "seniority path"}) is None
    # frază cu cifră
    assert orchestrator._keyword_screen(prof, {"title": "Dev", "description": "5+ years required"})


def test_keyword_screen_include_gate():
    prof = {"include_keywords": ["intern", "junior", "python"]}
    # niciun keyword de include → tăiat
    assert orchestrator._keyword_screen(prof, {"title": "Chef", "description": "cooking"}) == "no-include-match"
    # măcar unul apare (în descriere, nu titlu) → trece
    assert orchestrator._keyword_screen(prof, {"title": "Engineer", "description": "Python role"}) is None


async def test_scan_keyword_cut_skips_llm(jobs_db, monkeypatch):
    """Un job tăiat de exclude_keywords primește scor 0 FĂRĂ apel de model."""
    monkeypatch.setattr(orchestrator, "JOBS_MIN_SCORE", 6)
    monkeypatch.setattr(orchestrator, "JOBS_TOP_N", 5)
    prof = {**_PROFILE, "exclude_keywords": ["senior"]}
    _mock_scan(monkeypatch, [{"title": "Senior QA", "company": "c1", "description": "x"}])

    called = {"n": 0}
    async def _spy(profile, job):
        called["n"] += 1
        return 9
    monkeypatch.setattr(orchestrator, "_prefilter_score", _spy)

    res = await orchestrator._scan_profile(prof)
    assert res["selected"] == []          # tăiat → nu ajunge în digest
    assert called["n"] == 0               # LLM-ul nu a fost apelat deloc


# ── Pipeline scan → dedup → selecție ──────────────────────────────────────────

def _mock_scan(monkeypatch, jobs, errors=None):
    async def _fake(profile):
        return {"jobs": jobs, "errors": errors or []}
    monkeypatch.setattr(orchestrator, "_run_job_scan", _fake)


def _mock_scores(monkeypatch, scores):
    """scores = dict {title: score}."""
    async def _fake(profile, job):
        return scores.get(job.get("title"), 0)
    monkeypatch.setattr(orchestrator, "_prefilter_score", _fake)


async def test_scan_profile_selects_top_and_threshold(jobs_db, monkeypatch):
    monkeypatch.setattr(orchestrator, "JOBS_MIN_SCORE", 6)
    monkeypatch.setattr(orchestrator, "JOBS_TOP_N", 2)
    _mock_scan(monkeypatch, [
        {"title": "A", "company": "c1", "description": "x"},
        {"title": "B", "company": "c2", "description": "x"},
        {"title": "C", "company": "c3", "description": "x"},
        {"title": "D", "company": "c4", "description": "x"},
    ])
    _mock_scores(monkeypatch, {"A": 9, "B": 7, "C": 5, "D": 8})

    res = await orchestrator._scan_profile(_PROFILE)
    titles = [j["title"] for j in res["selected"]]
    assert titles == ["A", "D"]           # top-2 peste prag, sortate desc
    assert res["scanned"] == 4 and res["new"] == 4

    # C (sub prag) → skipped; A/D → sent
    def status(t):
        return jobs_db.execute("SELECT status FROM jobs WHERE title = ?", (t,)).fetchone()[0]
    assert status("A") == orchestrator._JOB_STATUS_SENT
    assert status("D") == orchestrator._JOB_STATUS_SENT
    assert status("C") == orchestrator._JOB_STATUS_SKIPPED


async def test_scan_profile_dedup_second_run(jobs_db, monkeypatch):
    monkeypatch.setattr(orchestrator, "JOBS_MIN_SCORE", 1)
    monkeypatch.setattr(orchestrator, "JOBS_TOP_N", 10)
    _mock_scan(monkeypatch, [{"title": "A", "company": "c1", "description": "x"}])
    _mock_scores(monkeypatch, {"A": 9})

    first = await orchestrator._scan_profile(_PROFILE)
    assert first["new"] == 1 and len(first["selected"]) == 1

    # Al doilea scan cu aceleași joburi → 0 noi, digest gol (nu re-trimite).
    second = await orchestrator._scan_profile(_PROFILE)
    assert second["new"] == 0 and second["selected"] == []


# ── Acțiuni pe job ────────────────────────────────────────────────────────────

def _insert_job(conn, jhash="h1", profile="stefan", status="sent"):
    conn.execute(
        "INSERT INTO jobs (hash, profile, title, company, url, description, status, first_seen) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (jhash, profile, "QA Intern", "Acme", "http://x", "desc", status, "2026-01-01"),
    )
    conn.commit()


def test_set_job_status(jobs_db):
    _insert_job(jobs_db)
    job = orchestrator._set_job_status("h1", orchestrator._JOB_STATUS_IGNORED)
    assert job is not None and job["title"] == "QA Intern"
    assert jobs_db.execute("SELECT status FROM jobs WHERE hash='h1'").fetchone()[0] == "ignored"


def test_set_job_status_missing(jobs_db):
    assert orchestrator._set_job_status("nope", "saved") is None


async def test_job_apply_spawns_careerops(jobs_db, monkeypatch):
    _insert_job(jobs_db)
    monkeypatch.setattr(orchestrator, "JOBS_PROFILES", [_PROFILE])
    calls = {}

    def _fake_launch(task_text, cwd, register_queue=False):
        calls["task_text"] = task_text
        calls["cwd"] = cwd
        return "task123", None
    monkeypatch.setattr(orchestrator, "_prepare_and_launch_task", _fake_launch)

    msg = await orchestrator._job_apply("h1")
    assert "task123" in msg
    assert "career-ops" in calls["task_text"].lower()
    assert "<job_posting>" in calls["task_text"]     # descriere încadrată ca DATE
    assert calls["cwd"].endswith("career-ops/stefan")
    assert jobs_db.execute("SELECT status FROM jobs WHERE hash='h1'").fetchone()[0] == "applied"


async def test_job_apply_reverts_on_block(jobs_db, monkeypatch):
    _insert_job(jobs_db)
    monkeypatch.setattr(orchestrator, "JOBS_PROFILES", [_PROFILE])
    monkeypatch.setattr(
        orchestrator, "_prepare_and_launch_task",
        lambda *a, **k: (None, "cwd blocat"),
    )
    msg = await orchestrator._job_apply("h1")
    assert "nu am putut" in msg.lower()
    # Nu marca fals ca 'applied' dacă career-ops n-a pornit → revine la 'saved'.
    assert jobs_db.execute("SELECT status FROM jobs WHERE hash='h1'").fetchone()[0] == "saved"


# ── Digest ────────────────────────────────────────────────────────────────────

async def test_digest_noop_without_gateway(monkeypatch):
    monkeypatch.setattr(orchestrator, "_tg_gateway", None)
    # Nu trebuie să arunce nici cu joburi prezente.
    await orchestrator._send_job_digest(_PROFILE, [{"hash": "h1", "title": "QA"}])


async def _fake_selected(monkeypatch):
    """Stub _scan_profile → un job selectat; înregistrează push-urile de digest."""
    async def _scan(p):
        return {"selected": [{"hash": "h", "title": "t"}], "scanned": 1, "new": 1, "errors": []}
    monkeypatch.setattr(orchestrator, "_scan_profile", _scan)
    sent = []
    async def _digest(p, jobs):
        sent.append(p.get("id"))
    monkeypatch.setattr(orchestrator, "_send_job_digest", _digest)
    return sent


async def test_scan_all_silent_no_auto_push(monkeypatch):
    prof = {"id": "contract-ai", "label": "🤝", "notify": "silent"}
    monkeypatch.setattr(orchestrator, "JOBS_PROFILES", [prof])
    sent = await _fake_selected(monkeypatch)
    # Scan automat (manual=False) → silent → NU se trimite digest
    summary = await orchestrator._job_scan_all(manual=False)
    assert sent == []
    assert summary["contract-ai"]["pushed"] is False


async def test_scan_all_silent_pushes_on_manual(monkeypatch):
    prof = {"id": "contract-ai", "label": "🤝", "notify": "silent"}
    monkeypatch.setattr(orchestrator, "JOBS_PROFILES", [prof])
    sent = await _fake_selected(monkeypatch)
    # `!scan contract-ai` (manual=True) → push forțat, chiar dacă e silent
    await orchestrator._job_scan_all("contract-ai", manual=True)
    assert sent == ["contract-ai"]


async def test_scan_all_push_profile_auto(monkeypatch):
    prof = {"id": "stefan", "label": "👔"}  # fără notify → default push
    monkeypatch.setattr(orchestrator, "JOBS_PROFILES", [prof])
    sent = await _fake_selected(monkeypatch)
    summary = await orchestrator._job_scan_all(manual=False)
    assert sent == ["stefan"]
    assert summary["stefan"]["pushed"] is True


async def test_digest_sends_card_per_job(monkeypatch):
    sent_cards = []

    class _Gw:
        async def send(self, *a, **k):
            pass
        async def send_job_card(self, job):
            sent_cards.append(job)

    monkeypatch.setattr(orchestrator, "_tg_gateway", _Gw())
    jobs = [{"hash": "h1", "title": "A"}, {"hash": "h2", "title": "B"}]
    await orchestrator._send_job_digest(_PROFILE, jobs)
    assert len(sent_cards) == 2
