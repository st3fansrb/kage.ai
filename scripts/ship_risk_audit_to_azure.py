#!/usr/bin/env python3
"""Trimite jurnalul de audit al hook-ului de risc în Azure Log Analytics.

Folosește Logs Ingestion API direct (REST), nu SDK-ul: fluxul e suficient de simplu
încât dependența în plus n-ar plăti, iar aici se vede exact ce se întâmplă —
client credentials flow → bearer token → POST pe DCE.

Traseul datelor:
    .logs/risk_audit.jsonl
      → POST {DCE}/dataCollectionRules/{DCR_ID}/streams/Custom-KageRisk_CL
      → DCR aplică transformarea KQL (extend TimeGenerated = todatetime(ts))
      → tabelul KageRisk_CL din workspace

Configurarea vine din mediu — niciun secret în cod sau în repo:
    AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET,
    AZURE_DCE_URI, AZURE_DCR_IMMUTABLE_ID
    AZURE_STREAM_NAME  (opțional, implicit Custom-KageRisk_CL)

Utilizare:
    set -a; source azure_lab.env; set +a
    python scripts/ship_risk_audit_to_azure.py --dry-run
    python scripts/ship_risk_audit_to_azure.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO / ".logs" / "risk_audit.jsonl"
DEFAULT_STREAM = "Custom-KageRisk_CL"
API_VERSION = "2023-01-01"
MAX_BATCH_BYTES = 900_000   # limita API e 1 MB necomprimat; lăsăm marjă

REQUIRED = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
            "AZURE_DCE_URI", "AZURE_DCR_IMMUTABLE_ID")


def load_config() -> dict:
    """Citește configurarea din mediu și eșuează explicit dacă lipsește ceva."""
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        print("[eroare] variabile de mediu lipsă:", ", ".join(missing), file=sys.stderr)
        print("         vezi azure_lab.env.example", file=sys.stderr)
        raise SystemExit(2)
    return {
        "tenant": os.environ["AZURE_TENANT_ID"],
        "client_id": os.environ["AZURE_CLIENT_ID"],
        "client_secret": os.environ["AZURE_CLIENT_SECRET"],
        "dce": os.environ["AZURE_DCE_URI"].rstrip("/"),
        "dcr": os.environ["AZURE_DCR_IMMUTABLE_ID"],
        "stream": os.environ.get("AZURE_STREAM_NAME", DEFAULT_STREAM),
    }


def get_token(cfg: dict) -> str:
    """Client credentials flow: schimbă client_id+secret pe un bearer token.

    Scope-ul are DOUĂ slash-uri — `https://monitor.azure.com//.default`. Nu e greșeală
    de tipar: resource URI-ul se termină în `/`, iar `.default` se lipește după el.
    Cu un singur slash primești un token care pare valid și e refuzat cu 401.
    """
    r = requests.post(
        f"https://login.microsoftonline.com/{cfg['tenant']}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "scope": "https://monitor.azure.com//.default",
        },
        timeout=30,
    )
    if r.status_code != 200:
        print(f"[eroare] autentificare eșuată ({r.status_code}): {r.text[:400]}", file=sys.stderr)
        raise SystemExit(1)
    return r.json()["access_token"]


def batches(events: list[dict], max_bytes: int = MAX_BATCH_BYTES):
    """Împarte evenimentele în loturi sub limita de mărime a API-ului."""
    cur: list[dict] = []
    size = 2  # parantezele drepte ale array-ului JSON
    for e in events:
        b = len(json.dumps(e, ensure_ascii=False).encode()) + 1
        if cur and size + b > max_bytes:
            yield cur
            cur, size = [], 2
        cur.append(e)
        size += b
    if cur:
        yield cur


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--dry-run", action="store_true",
                    help="arată ce s-ar trimite, fără să apeleze Azure")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"[eroare] {args.input} nu există — rulează întâi backfill_risk_audit.py",
              file=sys.stderr)
        return 1

    events = [json.loads(l) for l in args.input.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not events:
        print("[info] nimic de trimis")
        return 0

    lots = list(batches(events))
    total_kb = sum(len(json.dumps(b, ensure_ascii=False).encode()) for b in lots) / 1024
    print(f"{len(events)} evenimente · {len(lots)} lot(uri) · {total_kb:.1f} KB")

    if args.dry_run:
        print("[dry-run] nu s-a trimis nimic. Primul eveniment:")
        print(json.dumps(events[0], ensure_ascii=False, indent=2))
        return 0

    cfg = load_config()
    token = get_token(cfg)
    url = (f"{cfg['dce']}/dataCollectionRules/{cfg['dcr']}"
           f"/streams/{cfg['stream']}?api-version={API_VERSION}")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    sent = 0
    for i, lot in enumerate(lots, 1):
        r = requests.post(url, headers=headers,
                          data=json.dumps(lot, ensure_ascii=False).encode("utf-8"),
                          timeout=60)
        # 204 No Content = succes. Orice altceva e eșec.
        if r.status_code != 204:
            print(f"[eroare] lotul {i} respins ({r.status_code}): {r.text[:400]}", file=sys.stderr)
            return 1
        sent += len(lot)
        print(f"  lot {i}/{len(lots)}: {len(lot)} evenimente → 204 OK")

    print(f"[ok] {sent} evenimente trimise. Apar în KageRisk_CL în 3-10 minute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
