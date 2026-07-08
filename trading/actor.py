"""Actor — generator de IPOTEZE (WP-T, Etapa 5.1). Qwen 35B local, nocturn.

Rol strict (invariant #1): Actorul **NU scrie cod de execuție** și NU decide intrări/ieșiri.
Propune ≤3 ipoteze/noapte, fiecare un mecanism cauzal falsificabil, în format IMPUS:

    {
      "mecanism_cauzal":      "cine e forțat să facă ce și de ce, atunci",
      "predictie_cu_interval": {"kind":"point","mean":-1.5,"interval_80":[-6,2],"horizon":"48h"}
                               | {"kind":"prob","prob":0.62,"horizon":"7d"},
      "criteriu_falsificare": "ce rezultat o infirmă (cuantificat)",
      "implementare_schita":  "schiță în limbaj natural — NU cod"
    }

Output ne-conform e RESPINS (nu intră în registru). Propunerile valide se pre-înregistrează în
ledger (invariant #2) prin `trading.hypotheses` — momentul pre-înregistrării devine gardă temporală.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional, Sequence

from trading import hypotheses as H
from trading.ledger import TradingLedger
from trading.llm import ChatResult

REQUIRED_KEYS = ("mecanism_cauzal", "predictie_cu_interval", "criteriu_falsificare", "implementare_schita")

ACTOR_SYSTEM = (
    "Ești un cercetător cantitativ sceptic. Propui ipoteze despre piețele crypto (BTC/ETH) care "
    "descriu un MECANISM cauzal falsificabil (cine e forțat să tranzacționeze, de ce, când), NU "
    "pattern-uri de grafic. NU scrii cod. NU dai semnale de intrare/ieșire. Fiecare ipoteză trebuie "
    "să fie riscantă: o predicție cuantificată cu interval și un criteriu clar care ar infirma-o."
)

_INSTRUCTION = (
    "Propune cel mult {n} ipoteze. Răspunde DOAR cu un array JSON, fiecare obiect cu EXACT cheile: "
    "mecanism_cauzal (text), predictie_cu_interval (obiect: fie "
    "{{\"kind\":\"point\",\"mean\":float,\"interval_80\":[lo,hi],\"horizon\":\"48h\"}}, fie "
    "{{\"kind\":\"prob\",\"prob\":0..1,\"horizon\":\"7d\"}}), criteriu_falsificare (text), "
    "implementare_schita (text, NU cod). Fără explicații în afara JSON-ului."
)


class ActorFormatError(ValueError):
    """Output-ul Actorului nu respectă formatul impus (respins)."""


def build_messages(
    regime: Optional[str] = None,
    recent_failures: Optional[Sequence[str]] = None,
    n_max: int = 3,
) -> list[dict]:
    """Mesajele pentru Actor, condiționate pe regimul curent + eșecurile recente."""
    ctx = []
    if regime:
        ctx.append(f"Regimul curent de piață: {regime}.")
    if recent_failures:
        ctx.append("Ipoteze recent INFIRMATE (nu le repeta): " + "; ".join(recent_failures) + ".")
    user = (("\n".join(ctx) + "\n\n") if ctx else "") + _INSTRUCTION.format(n=n_max)
    return [
        {"role": "system", "content": ACTOR_SYSTEM},
        {"role": "user", "content": user},
    ]


def _extract_json_array(raw: str) -> list:
    """Extrage array-ul JSON din răspuns (tolerează fence-uri markdown / text în jur)."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Dacă tot mai e text în jur, ia de la primul '[' la ultimul ']'.
    if not text.startswith("["):
        i, j = text.find("["), text.rfind("]")
        if i != -1 and j != -1 and j > i:
            text = text[i:j + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ActorFormatError(f"Actorul nu a întors JSON valid: {exc}") from exc
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ActorFormatError("Actorul trebuie să întoarcă un array de ipoteze")
    return data


def validate_proposal(p: dict) -> None:
    """Ridică `ActorFormatError` dacă o propunere nu respectă formatul impus."""
    if not isinstance(p, dict):
        raise ActorFormatError("propunere non-obiect")
    for k in REQUIRED_KEYS:
        if k not in p:
            raise ActorFormatError(f"propunere fără cheia obligatorie '{k}'")
    for k in ("mecanism_cauzal", "criteriu_falsificare", "implementare_schita"):
        if not isinstance(p[k], str) or not p[k].strip():
            raise ActorFormatError(f"'{k}' trebuie să fie text ne-vid")
    # predicția reutilizează validatorul registrului de ipoteze (același contract).
    try:
        H._validate_prediction(p["predictie_cu_interval"])
    except H.HypothesisFormatError as exc:
        raise ActorFormatError(f"predictie_cu_interval invalidă: {exc}") from exc


def parse_proposals(raw: str, n_max: int = 3, strict: bool = True) -> list[dict]:
    """Parsează + validează propunerile. `strict=True` ridică la prima neconformă;
    `strict=False` le sare tăcut. Trunchiază la `n_max`.
    """
    items = _extract_json_array(raw)
    out: list[dict] = []
    for p in items:
        try:
            validate_proposal(p)
        except ActorFormatError:
            if strict:
                raise
            continue
        out.append(p)
        if len(out) >= n_max:
            break
    if not out:
        raise ActorFormatError("Actorul nu a produs nicio ipoteză conformă")
    return out


def propose(
    chat: Callable[[list[dict]], ChatResult],
    regime: Optional[str] = None,
    recent_failures: Optional[Sequence[str]] = None,
    n_max: int = 3,
    strict: bool = True,
) -> list[dict]:
    """Cheamă Actorul și întoarce propunerile conforme (≤ n_max). Nu atinge ledger-ul."""
    result = chat(build_messages(regime, recent_failures, n_max))
    return parse_proposals(result.content, n_max=n_max, strict=strict)


def register(
    ledger: TradingLedger,
    proposals: Sequence[dict],
    regime: Optional[str] = None,
    source_model: Optional[str] = None,
) -> list[int]:
    """Pre-înregistrează propunerile ca ipoteze (invariant #2). Întoarce id-urile create."""
    ids: list[int] = []
    for p in proposals:
        hid = H.register_hypothesis(
            ledger,
            mechanism=p["mecanism_cauzal"],
            prediction=p["predictie_cu_interval"],
            falsification=p["criteriu_falsificare"],
            regime_at_creation=regime,
            source_model=source_model,
        )
        ids.append(hid)
    return ids
