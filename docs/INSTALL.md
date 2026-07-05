# Kage — Installation Guide

Kage is a personal AI orchestration system: smart routing between local (Ollama) and cloud (Claude, Gemini) models, semantic caching, risk-gated terminal control, and a real-time UI.

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.10+ | [python.org](https://python.org) |
| Ollama | [ollama.ai](https://ollama.ai) — for local AI tiers |
| Claude CLI | `npm install -g @anthropic-ai/claude-code` — for agent tasks |
| Gemini CLI | Optional, for Gemini agent tasks |

## Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/kage.git
cd kage
bash scripts/setup.sh
```

`scripts/setup.sh` will:
1. Check Python version and dependencies
2. Create a `.venv` and install Python packages
3. Create `kage_config.json` from the example template

## Configuration

Edit `kage_config.json` (created by scripts/setup.sh):

```json
{
  "ntfy_topic": "kage-yourname-abc123",
  "api_token": "<run: openssl rand -hex 20>",
  "vault_path": "~/Documents/MyVault",
  "max_cloud_calls_per_day": 20,
  "autonomous_mode": false
}
```

**Required fields:**
- `ntfy_topic` — unique name for push notifications via [ntfy.sh](https://ntfy.sh). Pick anything like `kage-john-1a2b3c`.
- `api_token` — a secret token to protect your Kage instance. Generate with `openssl rand -hex 20`.

**Optional fields:**
- `vault_path` — path to your notes folder (Obsidian or any directory). Used by `!save`.
- `claude_cli` / `gemini_cli` — leave empty to auto-detect from PATH.
- `personal_keywords` — list of keywords that trigger context injection from your vault.
- `max_cloud_calls_per_day` — daily budget limit for cloud AI calls (default: 20).
- `autonomous_mode` — if `true`, medium-risk tool calls are approved automatically.

## Starting Kage

```bash
bash start_all.sh
```

This starts Ollama, LiteLLM (port 4000), and the Orchestrator (port 4001), then opens Kage in your browser at `http://localhost:4001/chat`.

```bash
bash scripts/stop_all.sh
```

Stops LiteLLM and the Orchestrator (Ollama keeps running as a shared service).

## Local AI Models

Kage uses two local tiers via Ollama:

```bash
ollama pull qwen3:8b    # Tier 1 — fast, lightweight tasks
ollama pull qwen3.6:35b # Tier 2 — complex local reasoning
```

The embed model for semantic caching:
```bash
ollama pull nomic-embed-text
```

## Chat Features

Open `http://localhost:4001/chat` and use prefix chips or type prefixes directly:

| Prefix | Effect |
|---|---|
| `!fast` | Force Tier 1 (qwen8b) |
| `!best` | Force Tier 5 (Claude Sonnet) |
| `!plan` | At least Tier 2 |
| `!run claude <task>` | Launch Claude agent in background |
| `!run gemini <task>` | Launch Gemini agent in background |
| `!save` | Save AI response to vault |
| `!nocache` | Skip semantic cache |
| `!status` | Instant system snapshot |
| `!help` | List all prefixes |

## Risk Gate

The `risk_hook.py` script acts as a PreToolUse hook for Claude Code. It evaluates every tool call on 3 axes: reversibility, explicit intent, and content sensitivity. Risky operations require your approval in the Kage UI before execution.

Configure it in `risk_settings.json`.

## Architecture

```
kage.html (UI)
    ↓  SSE / fetch
orchestrator.py (FastAPI, :4001)
    ↓  OpenAI-compatible
litellm (:4000)
    ↓
Ollama (:11434)   Claude API   Gemini API
```

## Troubleshooting

**`is the orchestrator running on :4001?`** — run `bash start_all.sh` first.

**Ollama circuit breaker active** — Ollama is down. Start it with `ollama serve` or restart with `bash start_all.sh`.

**ntfy notifications not working** — check `ntfy_topic` in `kage_config.json` and that it's unique.

**Claude CLI not found** — install with `npm install -g @anthropic-ai/claude-code` or set `claude_cli` path in config.

Logs are in `.logs/` (orchestrator.log, litellm.log, ollama.log).
