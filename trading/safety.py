"""Garda paper-only (WP-T, T1).

Criteriu de acceptare: *nicio cale de cod nu poate plasa un ordin real*. Această gardă
e apărarea programatică — orice config de trading trece prin `assert_paper_only` înainte
să fie folosit (ex. înainte de a scrie un config freqtrade sau a porni un daemon). Refuză:

- chei de credențiale live cu valoare (api_key/secret/password/private_key/token…);
- `dry_run: false` (freqtrade rulează real doar cu dry_run dezactivat);
- `trading_mode` diferit de paper/dry (ex. 'live').

E deliberat conservatoare: la orice îndoială, ridică `PaperOnlyViolation` și nu pornește
nimic. Dezactivarea ei = decizie manuală, în afara acestui sistem.
"""

from __future__ import annotations

from typing import Any

# Substring-uri de chei care indică credențiale de trading LIVE. Dacă apar cu o valoare
# nevidă în config, presupunem că cineva a pus chei reale → refuzăm.
_LIVE_CREDENTIAL_KEYS = (
    "api_key", "apikey", "api_secret", "secret", "private_key",
    "password", "passphrase", "uid",
)


class PaperOnlyViolation(RuntimeError):
    """Config-ul de trading ar putea permite tranzacții reale — refuzat."""


def _is_live_credential_key(key: str) -> bool:
    k = key.lower().replace("-", "_")
    return any(pat in k for pat in _LIVE_CREDENTIAL_KEYS)


def assert_paper_only(config: Any, _path: str = "") -> None:
    """Ridică `PaperOnlyViolation` dacă `config` ar putea permite trading real.

    Parcurge recursiv dict-uri/liste. Verifică: chei de credențiale live nevide,
    `dry_run` False, `trading_mode`/`mode` live. Fără efecte secundare.
    """
    if isinstance(config, dict):
        for key, value in config.items():
            path = f"{_path}.{key}" if _path else str(key)

            if _is_live_credential_key(str(key)):
                # Cheie de credențial live: e permisă doar goală (placeholder).
                if isinstance(value, str) and value.strip():
                    raise PaperOnlyViolation(
                        f"cheie de credențial live nevidă la '{path}' — paper-only interzice chei reale"
                    )
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    raise PaperOnlyViolation(f"cheie de credențial live cu valoare la '{path}'")

            if str(key).lower() == "dry_run" and value is False:
                raise PaperOnlyViolation(f"dry_run=false la '{path}' — trading real interzis")

            if str(key).lower() in ("trading_mode", "mode") and isinstance(value, str):
                if value.strip().lower() in ("live", "real", "production"):
                    raise PaperOnlyViolation(f"{key}='{value}' la '{path}' — doar paper/dry permis")

            assert_paper_only(value, path)

    elif isinstance(config, list):
        for i, item in enumerate(config):
            assert_paper_only(item, f"{_path}[{i}]")


def is_paper_only(config: Any) -> bool:
    """Variantă boolean (nu ridică) — utilă în UI/health check."""
    try:
        assert_paper_only(config)
        return True
    except PaperOnlyViolation:
        return False
