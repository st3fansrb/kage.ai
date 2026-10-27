"""Teste pentru auth_middleware (audit #1, #2, #9).

- /dashboard nu e exempt și nu livrează token-ul admin pe cookie (fosta pagină /jobs o făcea;
  a plecat în kage-labs);
- fără api_token în config, rutele neexempt răspund 401 (fail-closed);
- `?token=` în URL nu mai e acceptat; doar `Authorization: Bearer` sau cookie.
"""
import logging

import pytest
from fastapi.testclient import TestClient

import orchestrator

TOKEN = "s3cret-test-token"


@pytest.fixture
def with_token(monkeypatch):
    monkeypatch.setattr(orchestrator, "_get_api_token", lambda: TOKEN)


@pytest.fixture
def no_token(monkeypatch):
    monkeypatch.setattr(orchestrator, "_get_api_token", lambda: "")
    monkeypatch.setattr(orchestrator, "_auth_missing_token_warned", False)


@pytest.fixture
def client():
    return TestClient(orchestrator.app)


def _bearer(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


# ── /jobs (scos) și /dashboard ────────────────────────────────────────────────

def test_jobs_requires_auth(client, with_token):
    assert client.get("/jobs").status_code == 401


def test_dashboard_requires_auth(client, with_token):
    assert client.get("/dashboard").status_code == 401


def test_jobs_page_removed(client, with_token):
    assert client.get("/jobs", headers=_bearer()).status_code == 404


def test_dashboard_does_not_set_cookie(client, with_token):
    resp = client.get("/dashboard", headers=_bearer())
    assert resp.status_code == 200
    assert "kage_token" not in resp.headers.get("set-cookie", "")
    assert TOKEN not in resp.text


def test_dashboard_renders_with_empty_request_log(client, with_token):
    # `${agent}` din JS-ul paginii era interpolat de f-string-ul Python: fără niciun
    # request azi, variabila Python `agent` nu exista și /dashboard dădea 500.
    resp = client.get("/dashboard", headers=_bearer())
    assert resp.status_code == 200
    assert "${agent}" in resp.text


def test_visiting_dashboard_does_not_grant_api_access(client, with_token):
    client.get("/dashboard")
    client.get("/dashboard", headers=_bearer())
    assert "kage_token" not in client.cookies
    assert client.get("/api/config").status_code == 401


# ── fail-closed ───────────────────────────────────────────────────────────────

def test_no_token_configured_is_401(client, no_token, caplog):
    with caplog.at_level(logging.WARNING, logger=orchestrator.logger.name):
        assert client.get("/api/config").status_code == 401
        assert client.get("/api/config", headers=_bearer("")).status_code == 401
    assert "api_token" in caplog.text


def test_no_token_configured_exempt_still_open(client, no_token):
    assert client.get("/manifest.json").status_code == 200


# ── forma token-ului ──────────────────────────────────────────────────────────

def test_query_token_rejected(client, with_token):
    assert client.get(f"/api/config?token={TOKEN}").status_code == 401


def test_wrong_bearer_rejected(client, with_token):
    assert client.get("/api/config", headers=_bearer("gresit")).status_code == 401
    assert client.get("/api/config", headers={"Authorization": TOKEN}).status_code == 401


def test_bearer_accepted(client, with_token):
    assert client.get("/api/config", headers=_bearer()).status_code != 401


def test_cookie_accepted(with_token):
    c = TestClient(orchestrator.app, cookies={"kage_token": TOKEN})
    assert c.get("/api/config").status_code != 401
