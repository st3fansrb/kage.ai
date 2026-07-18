"""Teste pentru intent router-ul WP-NL (mesaje fără prefix `!`).

Acoperă: parsarea JSON a clasificatorului local (`_classify_intent`, mock httpx —
nicio dependență de Ollama viu), fail-safe pe eșec/încredere mică, și dispatch-ul
la handler-ele `!` EXISTENTE (`_route_intent`, cu handler-ele mockuite — testăm
rutarea, nu reimplementăm execuția).

`test_intent_classifier_accuracy_on_labeled_set` e singurul test care lovește
Ollama viu (qwen3:8b) — sărit automat dacă serviciul nu răspunde. Rulează-l
manual pentru calibrarea promptului/pragului (§8, „Stefan, ghidat").
"""
import json

import pytest

import orchestrator


# ── Fixturi ───────────────────────────────────────────────────────────────────

def _fake_ollama_content(monkeypatch, content: str):
    """Înlocuiește httpx.AsyncClient cu un client care întoarce `content` ca
    răspuns al Ollama (`/api/chat` → {"message": {"content": ...}})."""
    class _Resp:
        def json(self):
            return {"message": {"content": content}}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(orchestrator.httpx, "AsyncClient", lambda *a, **k: _Client())


def _boom_httpx(monkeypatch):
    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("network down")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(orchestrator.httpx, "AsyncClient", lambda *a, **k: _Boom())


@pytest.fixture(autouse=True)
def _reset_ollama_dead(monkeypatch):
    monkeypatch.setattr(orchestrator, "_ollama_dead", False)


# ── _classify_intent ────────────────────────────────────────────────────────

async def test_classify_intent_parses_valid_json(monkeypatch):
    _fake_ollama_content(monkeypatch, json.dumps(
        {"intent": "mission_new", "arg": "adaugă un endpoint de export CSV", "confidence": 0.9}
    ))
    intent, arg, conf = await orchestrator._classify_intent(
        "pornește o misiune care adaugă un endpoint de export CSV",
        has_draft=False, mission_active=False,
    )
    assert (intent, arg, conf) == ("mission_new", "adaugă un endpoint de export CSV", 0.9)


async def test_classify_intent_tolerates_surrounding_text(monkeypatch):
    # Modelele locale uneori adaugă text în jurul JSON-ului — regexul trebuie să-l extragă oricum.
    _fake_ollama_content(monkeypatch, 'Sigur, iată:\n{"intent": "status", "arg": "", "confidence": 0.8}\nGata.')
    intent, arg, conf = await orchestrator._classify_intent(
        "ce face sistemul acum?", has_draft=False, mission_active=False,
    )
    assert (intent, conf) == ("status", 0.8)


async def test_classify_intent_invalid_json_fails_safe(monkeypatch):
    _fake_ollama_content(monkeypatch, "nu știu ce vrei să spui")
    intent, arg, conf = await orchestrator._classify_intent(
        "ceva neclar", has_draft=False, mission_active=False,
    )
    assert (intent, arg, conf) == ("chat", "", 0.0)


async def test_classify_intent_unknown_label_fails_safe(monkeypatch):
    _fake_ollama_content(monkeypatch, json.dumps({"intent": "banane", "confidence": 0.9}))
    intent, arg, conf = await orchestrator._classify_intent(
        "ceva", has_draft=False, mission_active=False,
    )
    assert (intent, arg, conf) == ("chat", "", 0.0)


async def test_classify_intent_confidence_clamped_to_unit_interval(monkeypatch):
    _fake_ollama_content(monkeypatch, json.dumps({"intent": "jobs", "arg": "", "confidence": 5}))
    _, _, conf = await orchestrator._classify_intent("caută joburi", has_draft=False, mission_active=False)
    assert conf == 1.0


async def test_classify_intent_network_error_fails_safe(monkeypatch):
    _boom_httpx(monkeypatch)
    intent, arg, conf = await orchestrator._classify_intent(
        "ceva", has_draft=False, mission_active=False,
    )
    assert (intent, arg, conf) == ("chat", "", 0.0)


async def test_classify_intent_short_circuits_when_ollama_dead(monkeypatch):
    monkeypatch.setattr(orchestrator, "_ollama_dead", True)
    _boom_httpx(monkeypatch)  # dacă ar apela oricum, ar exploda — proba că nu apelează
    intent, arg, conf = await orchestrator._classify_intent(
        "ceva", has_draft=False, mission_active=False,
    )
    assert (intent, arg, conf) == ("chat", "", 0.0)


# ── _route_intent — fail-safe ──────────────────────────────────────────────────

async def test_route_intent_low_confidence_falls_through(monkeypatch):
    async def fake_classify(msg, **kw):
        return "mission_new", "ceva", 0.3   # sub prag
    monkeypatch.setattr(orchestrator, "_classify_intent", fake_classify)
    monkeypatch.setattr(orchestrator, "_latest_draft_id", lambda: None)
    resp = await orchestrator._route_intent("mesaj ambiguu")
    assert resp is None


async def test_route_intent_chat_intent_falls_through(monkeypatch):
    async def fake_classify(msg, **kw):
        return "chat", "", 0.95
    monkeypatch.setattr(orchestrator, "_classify_intent", fake_classify)
    monkeypatch.setattr(orchestrator, "_latest_draft_id", lambda: None)
    resp = await orchestrator._route_intent("ce mai faci")
    assert resp is None


# ── _route_intent — dispatch la handlerele existente ──────────────────────────

async def test_route_intent_mission_new_dispatches_to_mission_command(monkeypatch):
    async def fake_classify(msg, **kw):
        return "mission_new", "adaugă export CSV", 0.9
    calls = []
    async def fake_handle_mission(message):
        calls.append(message)
        return "SENTINEL"
    monkeypatch.setattr(orchestrator, "_classify_intent", fake_classify)
    monkeypatch.setattr(orchestrator, "_latest_draft_id", lambda: None)
    monkeypatch.setattr(orchestrator, "_handle_mission_command", fake_handle_mission)
    resp = await orchestrator._route_intent("pornește o misiune care adaugă export CSV")
    assert resp == "SENTINEL"
    assert calls == ["!mission new adaugă export CSV"]


async def test_route_intent_agent_run_dispatches_as_mission_draft(monkeypatch):
    """agent_run reutilizează fluxul de schiță cu butoane — nu execută direct (spec pas 4)."""
    async def fake_classify(msg, **kw):
        return "agent_run", "verifică dacă serverul e sus", 0.9
    calls = []
    async def fake_handle_mission(message):
        calls.append(message)
        return "SENTINEL"
    monkeypatch.setattr(orchestrator, "_classify_intent", fake_classify)
    monkeypatch.setattr(orchestrator, "_latest_draft_id", lambda: None)
    monkeypatch.setattr(orchestrator, "_handle_mission_command", fake_handle_mission)
    resp = await orchestrator._route_intent("rulează un task care verifică dacă serverul e sus")
    assert resp == "SENTINEL"
    assert calls == ["!mission new verifică dacă serverul e sus"]


async def test_route_intent_pending_draft_reroutes_to_revise(monkeypatch):
    async def fake_classify(msg, **kw):
        assert kw["has_draft"] is True
        return "mission_new", "schimbă pasul 2", 0.9
    calls = []
    async def fake_handle_mission(message):
        calls.append(message)
        return "SENTINEL"
    monkeypatch.setattr(orchestrator, "_classify_intent", fake_classify)
    monkeypatch.setattr(orchestrator, "_latest_draft_id", lambda: "draft123")
    monkeypatch.setattr(orchestrator, "_handle_mission_command", fake_handle_mission)
    resp = await orchestrator._route_intent("de fapt schimbă pasul 2")
    assert resp == "SENTINEL"
    assert calls == ["!mission revise schimbă pasul 2"]


async def test_route_intent_status_dispatches(monkeypatch):
    async def fake_classify(msg, **kw):
        return "status", "", 0.9
    monkeypatch.setattr(orchestrator, "_classify_intent", fake_classify)
    monkeypatch.setattr(orchestrator, "_latest_draft_id", lambda: None)
    monkeypatch.setattr(orchestrator, "_status_snapshot", lambda: "STATUS_SENTINEL")
    resp = await orchestrator._route_intent("ce face sistemul acum?")
    assert resp == "STATUS_SENTINEL"


async def test_route_intent_briefing_dispatches(monkeypatch):
    async def fake_classify(msg, **kw):
        return "briefing", "", 0.9
    async def fake_briefing():
        return "BRIEFING_SENTINEL"
    monkeypatch.setattr(orchestrator, "_classify_intent", fake_classify)
    monkeypatch.setattr(orchestrator, "_latest_draft_id", lambda: None)
    monkeypatch.setattr(orchestrator, "_handle_briefing_command", fake_briefing)
    resp = await orchestrator._route_intent("dă-mi rezumatul zilei")
    assert resp == "BRIEFING_SENTINEL"


async def test_route_intent_jobs_dispatches_with_arg(monkeypatch):
    async def fake_classify(msg, **kw):
        return "jobs", "stefan", 0.85
    calls = []
    async def fake_scan(message):
        calls.append(message)
        return "JOBS_SENTINEL"
    monkeypatch.setattr(orchestrator, "_classify_intent", fake_classify)
    monkeypatch.setattr(orchestrator, "_latest_draft_id", lambda: None)
    monkeypatch.setattr(orchestrator, "_handle_scan_command", fake_scan)
    resp = await orchestrator._route_intent("scanează joburi pentru profilul stefan")
    assert resp == "JOBS_SENTINEL"
    assert calls == ["!scan stefan"]


@pytest.mark.parametrize("verb", ["pause", "resume", "stop"])
async def test_route_intent_mission_control_dispatches(monkeypatch, verb):
    async def fake_classify(msg, **kw):
        return "mission_control", verb, 0.9
    calls = []
    async def fake_handle_mission(message):
        calls.append(message)
        return "CONTROL_SENTINEL"
    monkeypatch.setattr(orchestrator, "_classify_intent", fake_classify)
    monkeypatch.setattr(orchestrator, "_latest_draft_id", lambda: None)
    monkeypatch.setattr(orchestrator, "_handle_mission_command", fake_handle_mission)
    resp = await orchestrator._route_intent(f"{verb} misiunea")
    assert resp == "CONTROL_SENTINEL"
    assert calls == [f"!mission {verb}"]


async def test_route_intent_mission_control_unrecognized_arg_falls_through(monkeypatch):
    async def fake_classify(msg, **kw):
        return "mission_control", "abracadabra", 0.9
    monkeypatch.setattr(orchestrator, "_classify_intent", fake_classify)
    monkeypatch.setattr(orchestrator, "_latest_draft_id", lambda: None)
    resp = await orchestrator._route_intent("fă ceva cu misiunea")
    assert resp is None


async def test_route_intent_mission_steer_without_active_mission_falls_through(monkeypatch):
    async def fake_classify(msg, **kw):
        assert kw["mission_active"] is False
        return "mission_steer", "", 0.9
    monkeypatch.setattr(orchestrator, "_classify_intent", fake_classify)
    monkeypatch.setattr(orchestrator, "_latest_draft_id", lambda: None)
    monkeypatch.setattr(orchestrator, "_active_mission_id", None)
    resp = await orchestrator._route_intent("nu, mai bine altfel")
    assert resp is None


async def test_route_intent_mission_steer_with_active_mission_is_honest_not_silent(monkeypatch):
    async def fake_classify(msg, **kw):
        assert kw["mission_active"] is True
        return "mission_steer", "", 0.9
    monkeypatch.setattr(orchestrator, "_classify_intent", fake_classify)
    monkeypatch.setattr(orchestrator, "_latest_draft_id", lambda: None)
    monkeypatch.setattr(orchestrator, "_active_mission_id", "mission123")
    resp = await orchestrator._route_intent("nu, mai bine altfel")
    assert resp is not None
    body = "".join([chunk async for chunk in resp.body_iterator])
    assert "WP-AL" in body


async def test_route_intent_analytics_not_yet_available(monkeypatch):
    async def fake_classify(msg, **kw):
        return "analytics", "", 0.9
    monkeypatch.setattr(orchestrator, "_classify_intent", fake_classify)
    monkeypatch.setattr(orchestrator, "_latest_draft_id", lambda: None)
    resp = await orchestrator._route_intent("cât am cheltuit luna asta pe tier-uri?")
    assert resp is not None
    body = "".join([chunk async for chunk in resp.body_iterator])
    assert "WP-ETL" in body


# ── Set etichetat — calibrare accuratețe (opțional, necesită Ollama viu) ──────

_LABELED_PHRASES = [
    ("cât am cheltuit azi?", "status"),
    ("ce face sistemul acum?", "status"),
    ("dă-mi rezumatul zilei", "briefing"),
    ("ce s-a întâmplat azi, pe scurt?", "briefing"),
    ("pornește o misiune care adaugă un endpoint nou de export CSV", "mission_new"),
    ("construiește-mi un mic script care redenumește fișierele dintr-un folder", "mission_new"),
    ("rulează un task care verifică dacă serverul e sus", "agent_run"),
    ("pune misiunea pe pauză", "mission_control"),
    ("oprește misiunea curentă", "mission_control"),
    ("reia misiunea de unde a rămas", "mission_control"),
    ("caută joburi noi", "jobs"),
    ("scanează joburi pentru profilul tata", "jobs"),
    ("care e diferența dintre threading și asyncio în python?", "chat"),
    ("mulțumesc!", "chat"),
    ("ce zi e azi", "chat"),
]


def _ollama_reachable() -> bool:
    try:
        import httpx as _httpx
        r = _httpx.get(f"{orchestrator.OLLAMA_URL}/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_reachable(), reason="necesită Ollama viu (qwen3:8b) — pentru calibrare manuală")
async def test_intent_classifier_accuracy_on_labeled_set():
    correct = 0
    for phrase, expected in _LABELED_PHRASES:
        intent, _, conf = await orchestrator._classify_intent(
            phrase, has_draft=False, mission_active=False,
        )
        # Pentru "chat" fail-safe (eșec/JSON invalid → 0.0), intent corect contează chiar
        # dacă încrederea e mică — pragul de încredere gatează doar ACȚIUNEA din _route_intent,
        # nu corectitudinea clasificării măsurată aici.
        if intent == expected:
            correct += 1
    accuracy = correct / len(_LABELED_PHRASES)
    assert accuracy >= 0.7, f"Acuratețe {accuracy:.0%} sub pragul minim 70% pe setul etichetat"
