# Lab: Kage risk-gate telemetry in Microsoft Sentinel

Ingesting the audit trail of an LLM agent's policy enforcement point into a real SIEM,
and writing detection rules over it that map to MITRE ATT&CK.

Kage's `risk_hook.py` already decides, on every tool call an agent makes, whether to
allow, require human approval, or deny. Those decisions were only ever written as prose
into a Markdown journal — readable by a person, useless to a machine. This lab turns
them into queryable security telemetry.

## Pipeline

```
risk_hook.py  ──_audit_event()──▶  .logs/risk_audit.jsonl
                                          │
                     scripts/ship_risk_audit_to_azure.py
                     (client credentials → bearer token → POST)
                                          ▼
                             Data Collection Endpoint
                                          │
                        Data Collection Rule + KQL transform
                        (extend TimeGenerated = todatetime(ts))
                                          ▼
                          Log Analytics table  KageRisk_CL
                                          │
                            Microsoft Sentinel analytics rules
                                          ▼
                                     Incidents
```

## What was built

| Component | Name | Purpose |
|---|---|---|
| Resource group | `rg-kage-siem-lab` | blast radius — deleting it removes everything |
| Log Analytics workspace | `law-kage-lab` | storage + query engine |
| Data collection endpoint | `dce-kage-lab` | ingestion URL |
| Data collection rule | `dcr-kage-lab` | routing + ingestion-time transform |
| Custom table | `KageRisk_CL` (Analytics plan) | destination |
| Entra app registration | `app-kage-siem-shipper` | ingestion identity, `Monitoring Metrics Publisher` on the DCR |

The **Analytics** table plan was required, not preferred: Basic and Auxiliary tables
cannot carry scheduled analytics rules, which is the entire point of the exercise.

## Data

**481 events, 20 days of real use** — 480 recovered from the historical Markdown journal
via `scripts/backfill_risk_audit.py`, plus one live event confirming the new sink.

| Risk level | Count | Decision |
|---|---|---|
| Safe | 445 | allow |
| High | 26 | deny |
| Never | 7 | deny |
| Medium | 2 | 1 allow / 1 deny |

34 denials across 9 distinct days.

## Detection rules

| Rule | ATT&CK | Matches in baseline |
|---|---|---|
| [R1 — Destructive command blocked](R1-data-destruction.kql) | Impact (TA0040) / T1485 Data Destruction | 5 |
| [R2 — Unvetted package installation blocked](R2-supply-chain.kql) | Initial Access (TA0001) / T1195 Supply Chain Compromise | 2 |
| [R3 — Burst of denials in one hour](R3-denial-burst.kql) | Execution (TA0002) / T1059 Command and Scripting Interpreter | 1 window |

R2 is the detection counterpart to a preventive control already documented under
**LLM04:2026 Supply Chain** in [`../SECURITY-LLM-TOP10.md`](../SECURITY-LLM-TOP10.md) —
the same risk, covered both before and after the fact.

## Three things this lab actually taught

**1. Precision beats recall when a human triages the queue.** 24 of the 34 denials share
one cause — a `/dev/null` redirect flagged as high risk — which is benign nearly every
time. The naive burst rule fires on 6 windows, 5 of them noise. Excluding that single
pattern leaves 1 window, and it is a genuine cluster: `rm` in the home directory, a
`git push --force`, and deletion of source files, all inside one hour on 2026-06-02.
The exclusion is one line of KQL and it is the difference between a rule an analyst
would keep and one they would mute. The underlying fix belongs in the risk matrix, not
in the detection — the detection only made the problem visible.

**2. Backfilled data loses its own timestamps.** Azure Monitor overwrites `TimeGenerated`
with ingestion time for records older than a few days, so all 480 historical events
landed stamped "today". Because the hook writes its own `ts` field, the true event time
survived and all windowing is done on `ts`. Had the pipeline relied on the platform's
timestamp, three months of history would have collapsed into a single second.

**3. Authentication and authorization fail differently.** The app registration
authenticates successfully the moment it exists, and still gets `403` on ingestion until
`Monitoring Metrics Publisher` is granted **on the DCR** — not on the workspace. A valid
identity with no permission is the more common failure, and the one that looks like a
code bug.

## Reproducing

```bash
# 1. structured sink is written automatically by risk_hook.py from now on;
#    recover the historical journal:
python scripts/backfill_risk_audit.py --dry-run
python scripts/backfill_risk_audit.py

# 2. configure credentials (never committed — see azure_lab.env.example)
cp azure_lab.env.example azure_lab.env   # fill in, then:
set -a; source azure_lab.env; set +a

# 3. ship
python scripts/ship_risk_audit_to_azure.py
```

Useful discovery commands, since portal navigation changes:

```bash
az resource list --resource-type Microsoft.Insights/dataCollectionRules -o table
az resource show -g rg-kage-siem-lab -n dce-kage-lab \
  --resource-type Microsoft.Insights/dataCollectionEndpoints \
  --query properties.logsIngestion.endpoint -o tsv
az resource show -g rg-kage-siem-lab -n dcr-kage-lab \
  --resource-type Microsoft.Insights/dataCollectionRules \
  --query properties.immutableId -o tsv
```

## What this lab does not demonstrate

- **No live streaming.** Telemetry is shipped in batch from a file, not streamed. There
  is no agent or forwarder maintaining a cursor, and no delivery guarantee on restart.
- **No tuning over time.** The `/dev/null` exclusion is a single reasoned decision, not
  the result of iterating against analyst feedback across weeks.
- **No automated response.** No playbooks, no SOAR, no containment. Rules raise
  incidents; a human reads them.
- **Single data source.** One table, from one component. Real detection engineering
  correlates across identity, network, and endpoint telemetry.
- **The environment is gone.** The resource group was deleted after the evidence was
  captured, so the screenshots are the record. The KQL and the shipper are reproducible;
  the workspace is not.
