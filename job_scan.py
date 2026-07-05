#!/usr/bin/env python3.12
"""
job_scan.py — scanner de joburi standalone pentru Kage (WP-J).

Rulează în `.jobs-venv` (Python ≥3.10) fiindcă `python-jobspy` cere 3.10+, iar
nucleul Kage e pe 3.9. E apelat prin subprocess dintr-un job APScheduler din
orchestrator.py, NICIODATĂ importat de acesta.

Contract:
  - argv[1] = calea unui fișier JSON cu specul profilului (vezi mai jos).
  - stdout  = un singur obiect JSON: {"jobs": [...], "errors": [...]}.
  - stderr  = log (ignorat de apelant).
  - exit 0 chiar și la eșec parțial de scan; stdout rămâne JSON valid mereu.

Spec profil (câmpuri consumate):
  {
    "id": "stefan",
    "search_terms": ["QA intern", "junior software"],
    "sites": ["linkedin"],            # linkedin|indeed|glassdoor|google|zip_recruiter
    "location": "Bucharest, Romania",
    "results_wanted": 20,
    "hours_old": 72,
    "is_remote": false,
    "country_indeed": "romania"       # necesar doar pentru indeed/glassdoor
  }

SECURITATE (WP-J §3, §6): descrierile de joburi = conținut web ne-de-încredere.
Scriptul le tratează STRICT ca date — le serializează în JSON și atât. Nu execută,
nu evaluează și nu concatenează niciodată textul scanat ca instrucțiuni.
"""
from __future__ import annotations

import json
import sys
import traceback


def _emit(payload: dict) -> None:
    """Scrie un singur obiect JSON pe stdout și termină."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
    sys.stdout.flush()


def _clean(value) -> str:
    """Normalizează o celulă pandas (NaN/None → '') la string."""
    try:
        import pandas as pd  # local, ca eșecul de import să apară în errors
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            return ""
    except Exception:
        if value is None:
            return ""
    return str(value).strip()


def scan(profile: dict) -> dict:
    jobs: list[dict] = []
    errors: list[str] = []

    try:
        from jobspy import scrape_jobs
    except Exception as e:
        return {"jobs": [], "errors": [f"import jobspy failed: {e}"]}

    sites = profile.get("sites") or ["linkedin"]
    terms = profile.get("search_terms") or []
    location = profile.get("location") or ""
    results_wanted = int(profile.get("results_wanted", 20))
    hours_old = int(profile.get("hours_old", 72))
    is_remote = bool(profile.get("is_remote", False))
    country_indeed = profile.get("country_indeed") or "worldwide"

    seen_urls: set[str] = set()

    for term in terms:
        try:
            df = scrape_jobs(
                site_name=sites,
                search_term=term,
                location=location,
                results_wanted=results_wanted,
                hours_old=hours_old,
                is_remote=is_remote,
                country_indeed=country_indeed,
                linkedin_fetch_description=True,
                verbose=0,
            )
        except Exception as e:
            errors.append(f"scan '{term}': {e}")
            continue

        if df is None or getattr(df, "empty", True):
            continue

        for _, row in df.iterrows():
            url = _clean(row.get("job_url"))
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            jobs.append({
                "title": _clean(row.get("title")),
                "company": _clean(row.get("company")),
                "location": _clean(row.get("location")),
                "url": url,
                "site": _clean(row.get("site")),
                "date_posted": _clean(row.get("date_posted")),
                # Descriere = DATE ne-de-încredere. Trunchiată defensiv aici;
                # pre-filtrul din orchestrator o încadrează într-un bloc delimitat.
                "description": _clean(row.get("description"))[:4000],
                "search_term": term,
            })

    return {"jobs": jobs, "errors": errors}


def main() -> int:
    if len(sys.argv) < 2:
        _emit({"jobs": [], "errors": ["usage: job_scan.py <profile.json>"]})
        return 0
    try:
        profile = json.loads(open(sys.argv[1], encoding="utf-8").read())
    except Exception as e:
        _emit({"jobs": [], "errors": [f"read profile failed: {e}"]})
        return 0
    try:
        _emit(scan(profile))
    except Exception:
        _emit({"jobs": [], "errors": [f"unexpected: {traceback.format_exc()}"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
