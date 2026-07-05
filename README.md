# Kage

**Personal AI orchestration layer** — routes every request to the right model automatically, caches semantically, and gates risky operations before they run.

> Fast tasks → local Qwen · Complex tasks → Claude/Gemini · Dangerous operations → approval required

---

## What it does

Most AI tools force a choice: fast-but-dumb local models, or slow-but-capable cloud ones. Kage eliminates the tradeoff by routing automatically:

| Tier | Model | When |
|------|-------|------|
| T1 | Qwen 3 8B (local) | Quick questions, summaries |
| T2 | Qwen 3.6 35B (local) | Code, reasoning |
| T3 | Claude Haiku | Moderate tasks, context-aware |
| T4 | Gemini | Multimodal, large context |
| T5 | Claude Sonnet | Complex analysis, writing |
| T6 | Claude Opus | Critical decisions, best quality |

**Additional features:**
- **Semantic cache** — identical or near-identical questions hit ChromaDB instead of an LLM (configurable threshold, 24h TTL)
- **Daily budget** — hard limit on cloud calls per day, with warnings at 80%
- **Risk gate** — every Claude Code tool call (Bash, file ops) evaluated on 3 axes; risky operations require explicit approval in the UI
- **Push notifications** — approvals and alerts sent via [ntfy.sh](https://ntfy.sh)
- **Scheduled tasks** — `!schedule "0 9 * * 1" <task>` runs recurring agent tasks via cron
- **Real-time UI** — streaming responses, tier badge, live stats dashboard, pending approvals panel

---

## Requirements

- Python 3.10+
- [Ollama](https://ollama.ai) — for local AI tiers
- [Claude CLI](https://docs.anthropic.com/claude-code) — `npm install -g @anthropic-ai/claude-code` — for agent tasks
- Gemini CLI — optional

---

## Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/kage.git
cd kage
bash scripts/setup.sh
```

Edit `kage_config.json`, then:

```bash
bash start_all.sh
```

Kage opens at **http://localhost:4001/chat**

Pull the local models (first time only):
```bash
ollama pull qwen3:8b
ollama pull qwen3.6:35b
ollama pull nomic-embed-text
```

Full setup instructions: [INSTALL.md](docs/INSTALL.md)

---

## Configuration

`scripts/setup.sh` creates `kage_config.json` from the example. Key fields:

```json
{
  "ntfy_topic": "kage-yourname-abc123",
  "api_token":  "<openssl rand -hex 20>",
  "vault_path": "~/Documents/MyVault",
  "max_cloud_calls_per_day": 20,
  "autonomous_mode": false
}
```

See [kage_config.example.json](kage_config.example.json) for all options.

---

## Chat prefixes

| Prefix | Effect |
|--------|--------|
| `!fast` | Force Tier 1 (qwen8b) |
| `!best` | Force Tier 5 (Claude Sonnet) |
| `!plan` | At least Tier 2 |
| `!run claude <task>` | Background Claude agent |
| `!run gemini <task>` | Background Gemini agent |
| `!save [path]` | Save response to vault |
| `!nocache` | Skip semantic cache |
| `!status` | System snapshot (no LLM) |
| `!help` | All prefixes |

---

## Architecture

```
kage.html  (UI — chat + dashboard + approvals)
    │
    │  SSE + fetch
    ▼
orchestrator.py  (FastAPI :4001)
    │
    │  OpenAI-compatible
    ▼
litellm  (:4000)
    │
    ├── Ollama (:11434)   ← local models
    ├── Anthropic API     ← Claude
    └── Google AI         ← Gemini
```

---

## Risk Gate

`risk_hook.py` is a [Claude Code PreToolUse hook](https://docs.anthropic.com/claude-code/hooks) that intercepts every tool call and evaluates it on three axes:

1. **Reversibility** — can this be undone?
2. **Explicit intent** — did the user ask for this?
3. **Content** — does it touch sensitive files or paths?

High-risk calls are blocked until you approve them in the Kage UI or via the ntfy notification. Configure thresholds in `risk_settings.json`.

---

## License

[AGPL-3.0](LICENSE) — free for personal and open source use.

For commercial use (hosted service, closed-source product) without AGPL obligations: contact stefan.andrei.sirbu@gmail.com
