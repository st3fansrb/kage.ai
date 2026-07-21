"""Teste pentru conversia Markdown -> Telegram HTML la trimiterea răspunsurilor de chat.

Contract: răspunsurile LLM vin în Markdown; Telegram e configurat cu parse_mode=HTML, deci
`**bold**` trebuie convertit în `<b>bold</b>` înainte de trimitere, nu afișat literal.
"""
import telegram_gateway as tg


def _gateway():
    return tg.TelegramGateway(
        bot_token="t", chat_id="123", orchestrator_base_url="http://x", api_token="k"
    )


# ── _markdown_to_telegram_html: unitar ──────────────────────────────────────────

def test_bold_becomes_html_tag():
    assert tg._markdown_to_telegram_html("**cache hit**") == "<b>cache hit</b>"


def test_plain_text_without_markdown_is_unchanged_besides_escaping():
    assert tg._markdown_to_telegram_html("salut, ce faci?") == "salut, ce faci?"


def test_html_special_chars_are_escaped_outside_tags():
    out = tg._markdown_to_telegram_html("5 < 10 și **ok**")
    assert out == "5 &lt; 10 și <b>ok</b>"


def test_inline_code_preserves_underscored_identifier():
    out = tg._markdown_to_telegram_html("cheamă `_memory_retrieve()` din orchestrator")
    assert out == "cheamă <code>_memory_retrieve()</code> din orchestrator"
    # nu trebuie să apară italice/underscore-mangling pe identificator
    assert "<i>" not in out


def test_fenced_code_block_becomes_pre():
    out = tg._markdown_to_telegram_html("iată:\n```python\nx = 1\n```\ngata")
    assert "<pre>x = 1</pre>" in out
    assert "```" not in out


def test_markdown_link_becomes_anchor():
    out = tg._markdown_to_telegram_html("vezi [planul](https://example.com/plan)")
    assert out == 'vezi <a href="https://example.com/plan">planul</a>'


def test_unpaired_bold_marker_stays_literal_and_safe():
    out = tg._markdown_to_telegram_html("jumătate **bold fără închidere")
    assert "<b>" not in out
    assert "**bold fără închidere" in out


def test_cache_badge_style_text_converts_cleanly():
    out = tg._markdown_to_telegram_html("**[CACHE·T1·qwen8b]** răspuns din cache")
    assert out == "<b>[CACHE·T1·qwen8b]</b> răspuns din cache"


# ── _forward_to_orchestrator: integrare (HTTP mockuit) ──────────────────────────

async def test_forward_converts_markdown_before_sending(monkeypatch):
    gateway = _gateway()
    sent: list[str] = []

    async def fake_send(text, reply_markup=None):
        sent.append(text)

    monkeypatch.setattr(gateway, "send", fake_send)

    class _Resp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "starea e **ok** acum"}}]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            return _Resp()

    monkeypatch.setattr(tg.httpx, "AsyncClient", lambda *a, **k: _Client())

    await gateway._forward_to_orchestrator("!status")

    assert sent == ["starea e <b>ok</b> acum"]
    assert "**" not in sent[0]
