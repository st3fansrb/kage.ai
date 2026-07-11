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

## Remote access (Cloudflare Tunnel)

Reach the Mission Control UI from any device — phone included — with **no VPN and no client
install on the device**, just a browser. Only port `:3001` (the Next.js Mission Control) is
exposed; its server-side proxy talks to the orchestrator on `:4001` over localhost, so the
`api_token` never leaves the machine.

**One-time setup:**

```bash
brew install cloudflared
cloudflared tunnel login                        # authorize in browser
cloudflared tunnel create kage                  # prints TUNNEL_ID + writes credentials file
cloudflared tunnel route dns kage kage.example.com

cp cloudflare_tunnel.example.yaml cloudflare_tunnel.yaml   # fill in TUNNEL_ID + hostname
cp frontend/.env.local.example frontend/.env.local         # set KAGE_API_TOKEN
```

**Auth:** in the Cloudflare dashboard, Zero Trust → Access → Add a self-hosted application
for the hostname, with a policy allowing only your email (email OTP or Google). Without this,
the tunnel is publicly reachable.

**Run:**

```bash
bash scripts/start_frontend.sh   # builds once, serves Mission Control on :3001
bash scripts/start_tunnel.sh     # starts the Cloudflare tunnel
```

Then open `https://kage.example.com` on any device. Logs: `.logs/frontend.log`,
`.logs/tunnel.log`.

## Operating Kage from your phone (WP12)

The whole point of the Telegram remote workflow is "Stefan at work, laptop at home":
you create, approve and review missions from the phone. Two things must hold for this to work.

**Keep the machine awake (mandatory).** The Telegram gateway only polls while the process
runs, and macOS sleeps an idle laptop — which kills polling, and a sleeping Mac can't be woken
remotely. On AC power, disable idle sleep:

```bash
sudo pmset -c sleep 0        # never sleep on charger (clamshell/lid-closed on AC is fine)
pmset -g | grep sleep        # verify
```

`caffeinate -s` (held automatically while a mission runs, WP11) only covers active missions —
`pmset -c sleep 0` is what keeps Kage reachable when nothing is running.

**Create missions remotely.** From Telegram:

- `!mission new <direction>` — Kage drafts a `mission.md` plan and sends it back as a card with
  buttons: **✅ Pornește** (start), **✏️ Revizuiește** (reply with changes → it re-drafts),
  **🗑 Renunță** (discard). The draft is saved under `missions/<slug>/` with status `draft`
  until you approve it.
- `!mission revise <change>` — revise the most recent draft without pressing the button.
- `!mission status` / `!mission stop` — as before (WP11). The morning briefing (WP-D) now also
  lists active/paused/draft missions.

**Review missions from GitHub mobile (opt-in).** Set `remote.mission_git_branch: true` (default)
so each mission runs on its own `mission/<slug>` branch and commits the *full* diff (not just
`mission.md`). With `remote.mission_git_push: true` and a remote that has push auth (SSH key or
credential helper), Kage pushes after each work package and sends you a GitHub **compare** link
on Telegram — review the diff and merge from your phone. Commits use the repo's git identity
(Stefan), no Claude co-author trailer.

**Know when Kage goes silent (watchdog).** launchd restarts a dead process, but it can't tell
you when the network drops or the machine sleeps. Two safety nets:

- **Heartbeat** — set `remote.heartbeat_url` to a dead-man's-switch check (e.g.
  [healthchecks.io](https://healthchecks.io), free): Kage pings it every
  `remote.heartbeat_interval_min` minutes, and the external service alerts *you* when the pings
  stop. This is the only way to learn Kage is down when Kage itself can't message you.
- **Interrupted-job recovery** — a scheduled job (e.g. the job scan) that was killed mid-run by
  a restart is detected at the next startup, re-triggered, and announced on Telegram — no more
  silent losses like the 19:00 scan cut short by a restart.
- A `🟢 Kage online` message on Telegram at every startup (disable with
  `remote.startup_online_message: false`).

## Troubleshooting

**`is the orchestrator running on :4001?`** — run `bash start_all.sh` first.

**Ollama circuit breaker active** — Ollama is down. Start it with `ollama serve` or restart with `bash start_all.sh`.

**ntfy notifications not working** — check `ntfy_topic` in `kage_config.json` and that it's unique.

**Claude CLI not found** — install with `npm install -g @anthropic-ai/claude-code` or set `claude_cli` path in config.

Logs are in `.logs/` (orchestrator.log, litellm.log, ollama.log).
