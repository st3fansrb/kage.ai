"""Teste pentru cache-ul semantic context-aware (WP4/#8) — funcții pure de politică."""
import orchestrator


def _u(content):
    return {"role": "user", "content": content}


def _a(content):
    return {"role": "assistant", "content": content}


# ── _clean_cache_query — curăță prefixele (comportament c) ────────────────────

def test_clean_cache_query_strips_prefix():
    assert orchestrator._clean_cache_query("!best explică X") == "explică x"


def test_clean_cache_query_strips_multiple_prefixes():
    assert orchestrator._clean_cache_query("!nocache !best  Explică  X") == "explică x"


def test_clean_cache_query_strips_escaladeaza():
    assert orchestrator._clean_cache_query("escaladează rezolvă problema") == "rezolvă problema"


def test_clean_cache_query_no_prefix_lowercased():
    assert orchestrator._clean_cache_query("Salut Kage") == "salut kage"


def test_clean_cache_query_prefix_yields_same_key():
    # „!best explică X" și „explică X" trebuie să dea aceeași cheie de cache.
    assert orchestrator._clean_cache_query("!best explică X") == orchestrator._clean_cache_query("explică X")


# ── _cache_policy — follow-up (comportament a) ────────────────────────────────

def test_policy_single_turn_uses_cache():
    use_cache, store_ok, _ = orchestrator._cache_policy([_u("cât face 2+2")], "cât face 2+2")
    assert (use_cache, store_ok) == (True, True)


def test_policy_followup_disables_cache():
    msgs = [_u("cine e Ada Lovelace"), _a("..."), _u("continuă")]
    use_cache, store_ok, _ = orchestrator._cache_policy(msgs, "continuă")
    assert (use_cache, store_ok) == (False, False)


def test_policy_two_user_turns_is_followup():
    msgs = [_u("prima"), _u("a doua")]
    use_cache, _, _ = orchestrator._cache_policy(msgs, "a doua")
    assert use_cache is False


# ── _cache_policy — referenți temporali (comportament b) ──────────────────────

def test_policy_temporal_allows_lookup_but_blocks_store():
    for q in ("ce zi e azi", "ce oră e acum", "ce fac mâine", "ce am făcut ieri", "programul de astăzi"):
        use_cache, store_ok, _ = orchestrator._cache_policy([_u(q)], q)
        assert use_cache is True, q
        assert store_ok is False, q


def test_policy_temporal_diacritics_variants():
    for q in ("planul de maine", "sedinta de astazi"):
        _, store_ok, _ = orchestrator._cache_policy([_u(q)], q)
        assert store_ok is False, q


def test_policy_non_temporal_stores():
    _, store_ok, _ = orchestrator._cache_policy([_u("explică recursivitatea")], "explică recursivitatea")
    assert store_ok is True


# ── _cache_policy — prefixe care dezactivează cache-ul ────────────────────────

def test_policy_nocache_disables():
    use_cache, _, _ = orchestrator._cache_policy([_u("!nocache salut")], "!nocache salut")
    assert use_cache is False


def test_policy_retry_disables():
    use_cache, _, _ = orchestrator._cache_policy([_u("!retry salut")], "!retry salut")
    assert use_cache is False


def test_policy_returns_cleaned_query():
    _, _, q = orchestrator._cache_policy([_u("!best explică X")], "!best explică X")
    assert q == "explică x"
