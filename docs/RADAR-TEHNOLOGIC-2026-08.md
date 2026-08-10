# RADAR TEHNOLOGIC — august 2026

> Scan al peisajului extern (tehnologii noi, competiție, oportunități) raportat la starea reală
> a lui Kage din cod + `KAGE-HANDOFF.md`. Data scanului: **10.08.2026**.
>
> **Statut: informativ.** Nimic de aici nu e o decizie luată. Itemii care ating decizii deja
> închise în `KAGE-HANDOFF.md` §4 sunt marcați explicit ca *„redeschidere propusă"* — nu se
> execută fără aprobarea lui Stefan.
>
> **Fiabilitatea surselor:** verificate direct la sursa primară (blog oficial / GitHub) acolo
> unde e marcat ✅. Restul vin din agregatoare/SEO și sunt marcate ⚠️ — de re-verificat înainte
> de orice decizie.

---

## 0. Rezumat — ce merită atenție, în ordinea impactului

| # | Item | De ce contează pentru Kage | Efort | Verdict propus |
|---|---|---|---|---|
| 1 | **MLX vs Ollama pe Apple Silicon** | T1/T2 sunt pe Ollama; MLX e dat ca 20–50% mai rapid | mic (spike) | **măsoară** |
| 2 | **Airflow 3.x** (Kage e pe 2.10.5) | keyword de CV + arhitectură nouă | mediu | **evaluează** |
| 3 | **`purgedcv` / `pypbo`** | fac exact ce cere Quant Lab Etapa 1.1 | mic | **oracol de test** |
| 4 | **OTel GenAI semconv** | alternativă vendor-neutră la WP7 (Phoenix) | mediu | **redeschidere propusă** |
| 5 | **`apple/container` 1.0 → 1.2.x** | motivul amânării la WP-G2 („imatur") a slăbit | mediu | **notă, nu acțiune** |
| 6 | **MCP spec 2026-07-28** | Kage NU consumă MCP azi → oportunitate, nu datorie | mare | **doar de urmărit** |
| 7 | **OpenClaw (~386k ⭐)** | același segment de produs ca Kage | — | **poziționare** |
| 8 | **Harbor / Terminal-Bench 2.0** | format de task standard pentru KageBench | mic | **de citit** |
| 9 | **Hackathoane + granturi** | ținte concrete cu deadline | — | **acțiune** |

---

## 1. Tehnologii cu impact direct pe cod

### 1.1 MLX ca runtime local, alternativ la Ollama ⚠️

Sursele din 2026 dau **MLX cu 20–30% peste llama.cpp și până la ~50% peste Ollama** pe Apple
Silicon, iar familiile folosite de Kage (Qwen) au versiuni MLX cuantizate pe HuggingFace.

**De ce e relevant acum:** T1 (`qwen3:8b`) și T2 (`qwen3.6:35b`) sunt ambele pe Ollama, iar T2
e tier-ul pe care cade *tot* fallback-ul de buget — deci latența lui e latența sistemului în
ziua în care plafonul cloud e atins. Există deja `docs/R0-LATENCY-REPORT.md` ca bază de
comparație.

**Ce NU susțin sursele:** că migrarea e gratis. Ollama dă un API stabil pe care LiteLLM îl
consumă deja; MLX ar cere un strat de servire în plus (`mlx-server` sau echivalent) și mută
Kage de pe o dependință matură pe una mai tânără.

**Propunere onestă:** un spike de o seară care măsoară T2 pe același prompt set în ambele
runtime-uri, *fără* să schimbe nimic în producție. Dacă diferența e sub ~20% end-to-end (nu
tokens/s izolat), nu merită. Decizia se ia pe cifre proprii, nu pe blogpost-uri.

### 1.2 Airflow 3.x — Kage e pe 2.10.5 ⚠️

`KAGE-HANDOFF.md:1613` fixează `apache-airflow[postgres]==2.10.5`. Între timp linia 3.x e
livrată, cu: arhitectură service-oriented, **Task Execution API** (task-urile nu mai ating
metadata DB direct — izolare reală față de core), **DAG versioning** nativ, **Assets** ca
obiect de primă clasă și **event-driven scheduling**, plus UI rescris.

**De ce contează dublu:**
- **tehnic** — Task Execution API se leagă exact de amendamentul GCP din WP-G2 (execuție
  efemeră per-task, în afara mașinii): un worker decuplat de metadata DB e mult mai ușor de
  rulat în Cloud Run decât unul din 2.x;
- **CV** — JD-ul Revolut cere Airflow; a demonstra 3.x cu DAG versioning și assets e diferit
  de a demonstra un DAG 2.x clasic.

**Capcană:** upgrade-ul 2→3 e breaking (execuția task-urilor, providerii, config). WP-AF e
livrat și verde; un upgrade prost făcut strică un WP terminat. Dacă se face, se face pe branch
separat, cu testele WP-AF ca gate.

### 1.3 `apple/container` a ajuns matur — dar decizia WP-G2 rămâne valabilă ✅

`apple/container` a atins **1.0 pe 9 iunie 2026** (prima versiune stabilă de la lansarea de la
WWDC 2025) și e acum pe linia **1.2.x**, cu: micro-VM dedicat per container, scris în Swift
pentru Apple Silicon, plugin `k8s` pentru cluster local, export OCI, `--read-only-path` /
`--masked-path`, config TOML.

**Raportare corectă la decizia existentă:** recomandarea din handoff (04.07.2026) a fost Docker
on-demand, cu `apple/container` „de revizitat peste ~1 an" — adică **~iulie 2027, termen care
NU a expirat**. Ce s-a schimbat e *motivul* amânării, nu calendarul: obiecția era „tooling mai
tânăr decât Docker", iar 1.0 + 1.2.x cu path controls și micro-VM per container o slăbește
sensibil, mai devreme decât se aștepta.

**Propunere:** nicio acțiune acum. Trigger-ul WP-G2 rămâne cel de capabilitate (browser MCP sau
rulări zilnice nesupravegheate), nu de calendar. Când acel trigger se declanșează, re-evaluarea
Docker vs `apple/container` se face cu datele din 2026, nu cu cele din iulie 2025 —
`--read-only-path` / `--masked-path` acoperă direct nevoia de „credențiale montate read-only"
din specul WP-G2.

### 1.4 Specificația MCP 2026-07-28 — oportunitate, nu datorie tehnică ✅

Verificat în cod: **Kage nu consumă și nu expune servere MCP azi.** Singura apariție e un
matcher de risc (`risk_settings.example.json:5`, `mcp__.*`), pregătit pentru viitor. Deci
schimbările de mai jos **nu creează muncă de migrare** — contează doar dacă/când Kage intră în
ecosistem.

Ce s-a schimbat în spec-ul din 28.07.2026:
- **nucleu stateless** — request/response pur; orice cerere poate ajunge pe orice instanță în
  spatele unui load balancer round-robin banal;
- **eliminat**: handshake-ul `initialize`/`initialized`, header-ul `Mcp-Session-Id`, conceptul
  de sesiune la nivel de protocol;
- **routing pe headere** (`Mcp-Method`, `Mcp-Name`) — gateway-urile rutează fără să parseze JSON;
- **MRTR** (multi round-trip requests) — un tool poate cere input de la user la mijlocul
  apelului (`resultType: "input_required"`), fără stream ținut deschis;
- **rezultate de listă cacheabile** (`ttlMs`, `cacheScope`);
- **autorizare**: RFC 9207 obligatoriu, DCR → CIMD;
- **deprecate cu sunset la 12 luni**: Roots, Sampling, Logging, transportul HTTP+SSE legacy;
- **Tasks** iese din core într-o extensie (`io.modelcontextprotocol/tasks`).

**Observația care contează pentru Kage:** MRTR („tool-ul cere aprobarea userului la mijlocul
apelului, prin retry, fără stare pe server") e *exact* forma pe care o are deja gate-ul de risc
cu aprobări pending din `risk_hook.py` + `/risk/*`. Dacă Kage expune vreodată capabilitățile
proprii ca server MCP, semantica de HITL nu trebuie reinventată — se mapează pe MRTR.

**Verdict:** de urmărit, zero acțiune acum. Nu adăuga MCP „ca să fie".

### 1.5 Modelele locale — T2 e la zi, T1 poate nu ⚠️

`qwen3.6:35b` (T2) e generația curentă — bine. Ce a apărut în plus și n-are corespondent în
Kage: **Qwen3-Coder-Next** (feb. 2026, raportat ~44.3% pe SWE-Bench Pro) și linia
`qwen3-coder:30b` (MoE, ~3.3B activi, ~19GB Q4, 256K context).

Kage rutează *codul* pe T2 generalist. Un tier de cod dedicat, MoE cu puțini parametri activi,
ar putea fi mai rapid **și** mai bun pe exact clasa de taskuri unde T2 e cel mai solicitat
(WP-SD, agenții `!run`). Contra: încă un model de 19GB rezident pe o mașină unde Qwen ține deja
~25GB — ori îl încarci la cerere, ori înlocuiești, nu adaugi.

---

## 2. Observabilitate — redeschidere propusă pentru WP7

WP7 („Phoenix peste LiteLLM") e amânat, de făcut împreună cu WP10. Între timp peisajul s-a
mutat: **OpenTelemetry GenAI semantic conventions** s-au impus ca strat comun — namespace
`gen_ai.*`, cu `gen_ai.provider.name`, `gen_ai.operation.name`, `gen_ai.request.model`,
`gen_ai.usage.input_tokens` / `output_tokens`, plus span-uri dedicate de *agent* și de *tool*.
Langfuse, Arize AX și Phoenix vorbesc toate OTel. ⚠️ Convențiile sunt însă **încă
`experimental`** (stare raportată în martie 2026) — deci nu sunt un contract stabil.

**Argumentul pentru redeschidere:** Kage are deja run ledger (WP8), telemetrie în Postgres
(WP-PG) și pipeline de analytics (WP-ETL). Emiterea de span-uri `gen_ai.*` peste ce există
transformă telemetria proprietară într-una **standard**, cu două consecințe: (a) orice backend
(Phoenix, Langfuse, Datadog) devine plug-in, nu rescriere; (b) e un keyword de CV mai puternic
decât „am pornit Phoenix", fiindcă demonstrează instrumentare, nu instalare.

**Argumentul contra, la fel de real:** semconv experimentale înseamnă atribute care se pot
redenumi; iar Kage nu are problema pe care o rezolvă observabilitatea la scară (un singur user,
un singur nod). Costul e nenul, beneficiul operațional imediat e mic.

**Propunere:** nu înlocui WP7, ci **reformulează-l** — „export OTel GenAI din run ledger" în loc
de „Phoenix peste LiteLLM", cu Phoenix/Langfuse ca simplu consumator ales la final. Rămâne
amânat până la WP10, ca acum.

---

## 3. Quant Lab — există librării care fac Etapa 1.1

Backlog-ul cere `trading/validation.py` scris de la zero: `bootstrap_pnl`, `permutation_test`,
`deflated_sharpe`, `pbo_cscv`. Există deja: ⚠️

- **`purgedcv`** — compatibil scikit-learn: purged / group-purged / **combinatorial purged CV
  (CPCV)**, walk-forward splitting, și statistici de overfitting: **DSR, PSR, PBO, minimum
  backtest length**;
- **`pypbo`** — implementare dedicată de Probability of Backtest Overfitting;
- `awesome-quant` rămâne indexul de referință.

**Aici recomandarea e nuanțată, nu „folosește librăria".** Invariantul #5 spune că validarea e
matematică, iar acceptarea de la 1.1 cere test cu *serie sintetică de semnal cunoscut* — o
strategie „câștigătoare fabricată din zgomot" trebuie să pice permutation + DSR. Dacă imporți
librăria, ai testat librăria, nu înțelegerea ta; iar pista de învățare (§8 din handoff, mod
interviu) tocmai asta vrea să producă.

**Propunere:** scrie `validation.py` cum e planificat, dar folosește `purgedcv` ca **oracol de
test** — aceleași intrări, aceleași ieșiri în limita toleranței. Câștigi corectitudine fără să
pierzi înțelegerea. Bonus: CPCV și *minimum backtest length* sunt două lucruri pe care
backlog-ul nu le are și care merită adăugate în spec.

---

## 4. Evaluare — KageBench are un format standard disponibil ⚠️

`kagebench-taskbank-draft.md` definește 14 cazuri cu harness propriu. Între timp:
**Terminal-Bench 2.0** (task-uri reale de linie de comandă în containere Docker izolate, 16
categorii) rulează pe **Harbor**, un harness de evaluare la scară care suportă nativ Claude
Code, Codex CLI, OpenHands, Mini-SWE-Agent.

**Relevanță:** nu ca să înlocuiască KageBench — cazurile KB-01…KB-14 testează *rutarea, cache-ul,
bugetul și gate-ul de risc*, adică lucruri pe care niciun benchmark public nu le acoperă. Dar
formatul de task Harbor și felul în care izolează + punctează sunt gratis de împrumutat, iar
G1-minim e deja livrat, deci refactorul e ieftin.

---

## 5. Competiția — OpenClaw ✅

**OpenClaw** (fost Clawdbot/Moltbot): **~386k stele, ~81k fork-uri, licență MIT**, verificat
direct pe GitHub. Este același segment de produs ca Kage:

| Dimensiune | OpenClaw | Kage |
|---|---|---|
| Model de rulare | self-hosted, orice OS | self-hosted, macOS |
| Canale | WhatsApp, Telegram, Slack, Discord, Signal, iMessage, 22+ | Telegram + web UI |
| Arhitectură | Gateway ca control plane + Control UI / CLI / TUI | FastAPI + Mission Control (Next.js) |
| Extensibilitate | plugin SDK, skills, companion apps | prefixe, misiuni, agenți SDK |
| Modele | hosted + local | rutare 6-tier local/cloud |
| Licență | MIT | privat |

**Citirea onestă a situației.** Kage nu câștigă o competiție de features cu un proiect cu 81k
fork-uri, și nici nu are de ce — e single-user, nu produs. Dar două lucruri se schimbă:

1. **Ce nu mai merită construit.** Multi-channel (WhatsApp/Discord/Signal) e teren cucerit.
   Ideea „Kage Terminal" (aplicație desktop + DMG) intră în aceeași categorie: OpenClaw are deja
   Control UI + CLI + TUI + companion apps. Efortul e mai bine pus în ce Kage are și OpenClaw nu.
2. **Ce e de fapt diferențiatorul.** Din compararea de mai sus, lucrurile pe care OpenClaw nu le
   are ca invariant de design sunt exact coloana vertebrală a lui Kage: **rutarea pe cost cu
   plafon de buget și fallback local**, **gate-ul de risc cu aprobare** ca precondiție de
   execuție, **run ledger + decision trace**, și **disciplina anti-overfitting** din Quant Lab.
   Astea sunt și lucrurile care se prezintă bine la un interviu de inginerie — nu „vorbește cu
   botul pe WhatsApp".

**Propunere de poziționare:** Kage nu e „un asistent personal open-source" (piață pierdută), ci
**un plan de control guvernat pentru workload-uri AI** — rutare pe cost, policy enforcement,
audit trail, evaluare. Coincide cu reorientarea deja scrisă în `REVOLUT-INTERNSHIP-ALIGNMENT.md`,
ceea ce e un semn bun: decizia era deja corectă, acum are și o justificare competitivă.

Menționate în aceeași categorie, mai mici: **QwenPaw** (ecosistem Qwen, multi-agent) și
**PicoClaw** (Go, <10MB RAM). ⚠️

---

## 6. Oportunități cu deadline

### 6.1 Granturi și programe Anthropic ⚠️ (de verificat pe site-ul oficial)

- **Anthropic Economic Futures — Research Awards**: granturi de **$10.000–$50.000** pentru
  cercetare empirică pe impactul economic al AI; livrabil = prezentarea rezultatelor în ~6 luni.
  Include credite API și parteneriate.
- **Anthropic Fellows Program** — workstream Economics & Policy, aplicații pe bază continuă,
  următoarea cohortă anunțată pentru **finalul lui septembrie 2026**; cohorta de cercetare pe
  AI safety are aplicații deschise pentru **noiembrie 2026**.
- **Claude Corps** — program nou, de verificat condițiile.

**Aplicabilitate realistă:** Economic Futures cere cercetare economică empirică, nu un proiect
de inginerie — Kage nu se califică așa cum e. Ce *ar putea* produce date de calificare e
telemetria WP-ETL: un sistem care înregistrează, pe luni, ce fracțiune din munca reală a unei
persoane a fost rutată către modele locale vs cloud, la ce cost, e exact tipul de date primare
care lipsesc din literatura pe „AI și productivitate". Nu e o aplicație gata; e singurul unghi
credibil.

### 6.2 Hackathoane ⚠️

| Eveniment | Când | Notă |
|---|---|---|
| **The Bucharest Hackathon** (ed. 3) | 2026, București | AI + dev tools; local, cost de participare ~zero |
| **European AI Hackathon** | 6–29 oct. 2026 | format lung (3 săptămâni), SME/startup/cercetare |
| **AI & Big Data Expo Europe** | 16–20 oct. 2026 | hibrid; AI, cloud, data infrastructure |
| **Ruya AI — Self-Improving Agents** | 2026 | temă: agenți care se auto-îmbunătățesc |
| **HackEurope** | ed. viitoare | Dublin / Paris / Stockholm, 1000 studenți |
| **CISPA European Cybersecurity & AI** | regional → finală (DE) | pentru studenți |

**Cel mai bun fit, obiectiv: Ruya AI — Self-Improving Agents.** WP-SD („Kage lucrează la Kage"
de pe Telegram) e literal un agent care se auto-modifică, e **deja implementat** (15.07.2026), și
are ceva ce majoritatea submisiilor la o astfel de temă nu au: un gate de risc care îl oprește.
Un demo de auto-îmbunătățire *guvernată* e mai interesant decât încă unul de auto-îmbunătățire
nelimitată.

Al doilea ca fit: **European AI Hackathon** — formatul de 3 săptămâni se potrivește cu ritmul
„seri și weekenduri" din handoff, spre deosebire de un sprint de 30h.

### 6.3 Protocoale ca semnal de piață ✅⚠️

**A2A** (Agent2Agent, Linux Foundation) a trecut de **150+ organizații**, ~22.000 stele, SDK-uri
în 5 limbaje, integrat la Google/Microsoft/AWS. ⚠️ Unii analiști notează că cifrele de titlu
supraestimează utilizarea zilnică reală.

Pentru Kage single-user, A2A nu rezolvă nicio problemă existentă. Contează doar ca semnal: MCP
(tool↔agent) și A2A (agent↔agent) sunt cele două keyword-uri de infrastructură pe care le cere
piața în 2026. Dacă la un moment dat vrei un item de CV pe zona asta, **Kage ca server MCP** e
de departe cel mai ieftin (expune rutarea/memoria/ledger-ul ca tools) și se mapează pe MRTR
pentru aprobări, cum e notat la §1.4.

---

## 7. Ce NU s-a schimbat

Ca să nu rămână impresia că totul trebuie revizuit:

- **Redpanda single-node** pentru WP-KF rămâne alegerea corectă (Kafka API, un binar). Nimic nou
  nu o contrazice; amânarea rămâne justificată.
- **Postgres** ca stare partajată (WP-PG) — nicio mișcare care s-o repună în discuție. DuckDB
  apare mult în 2026, dar ca motor analitic *embedded*, complementar, nu înlocuitor; ar avea sens
  eventual în WP-ETL, nu în locul Postgres.
- **Paper-only + invarianții Quant Lab** — nimic din cercetarea de anul asta nu slăbește teza
  anti-overfitting; dimpotrivă, apariția `purgedcv` și literatura pe backtest overfitting o
  întăresc.
- **Claude Agent SDK** (WP9) rămâne runtime-ul potrivit: Skills (SKILL.md, progressive
  disclosure), subagenți cu context propriu, iar din iunie 2026 planurile de abonament includ un
  credit lunar separat de Agent SDK (⚠️ $20 Pro / $100 Max 5x / $200 Max 20x) — de verificat, ar
  putea schimba economia rulărilor `!run`.

---

## 8. Dacă ar fi să alegi trei lucruri

1. **Spike-ul MLX** (o seară) — singurul item cu câștig imediat, măsurabil, și cu risc zero dacă
   rezultatul e negativ: nu schimbi nimic.
2. **`purgedcv` ca oracol pentru Etapa 1.1** (fără efort suplimentar) — se strecoară în muncă
   deja planificată și îi crește corectitudinea.
3. **Ruya AI Self-Improving Agents cu WP-SD** — singura oportunitate externă unde ai deja
   livrabilul construit; costul e de prezentare, nu de inginerie.

Restul (Airflow 3, OTel, MCP, `apple/container`) sunt reale, dar niciunul nu e urgent, și
fiecare atinge un WP terminat sau o decizie închisă — deci merg pe branch separat, cu aprobare,
nu oportunist.

---

## Surse

- MCP: [spec 2026-07-28](https://blog.modelcontextprotocol.io/posts/2026-07-28/) · [roadmap 2026](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/) · [ghid de migrare AAIF](https://aaif.io/blog/mcp-2026-07-28-whats-changing-and-how-to-migrate)
- Izolare: [apple/container releases](https://github.com/apple/container/releases) · [Apple container 1.0](https://agent-wars.com/news/2026-06-12-apple-container-1-0-microvm) · [state of microVM isolation 2026](https://emirb.github.io/blog/microvm-2026/) · [awesome-agent-sandbox](https://github.com/fishman/awesome-agent-sandbox)
- Local LLM: [M5 Pro & Max local LLM](https://modelfit.io/blog/m5-pro-max-local-llm-2026/) · [best local LLMs Mac 2026](https://insiderllm.com/guides/best-local-llms-mac-2026/) · [Qwen models mid-2026](https://insiderllm.com/guides/qwen-models-guide/) · [Qwen3-Coder-Next](https://dev.to/sienna/qwen3-coder-next-the-complete-2026-guide-to-running-powerful-ai-coding-agents-locally-1k95)
- Observabilitate: [OTel GenAI semconv](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions) · [comparativ platforme 2026](https://www.marktechpost.com/2026/08/09/top-llm-observability-and-evaluation-platforms-in-2026-langfuse-langsmith-braintrust-arize-and-more-compared/) · [Langfuse vs Phoenix](https://qaskills.sh/blog/langfuse-vs-phoenix-arize)
- Data stack: [Airflow 3 — what's new](https://versionlog.com/apache-airflow/3/) · [Airflow 3.3 release notes](https://airflow.apache.org/docs/apache-airflow/stable/release_notes.html)
- Quant: [awesome-quant](https://github.com/wilsonfreitas/awesome-quant) · [pypbo](https://github.com/esvhd/pypbo) · [Deflated Sharpe Ratio (Bailey & López de Prado)](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) · [backtest overfitting in the ML era](https://www.sciencedirect.com/science/article/abs/pii/S0950705124011110)
- Evaluare: [Terminal-Bench (ICLR 2026)](https://openreview.net/pdf/417ac3236de7dbf3fc3414c51754dd239271663e.pdf) · [SWE-bench vs Terminal-Bench](https://www.digitalapplied.com/blog/swe-bench-terminal-bench-benchmark-guide-2026)
- Competiție: [OpenClaw pe GitHub](https://github.com/openclaw/openclaw) · [awesome-openclaw](https://github.com/SamurAIGPT/awesome-openclaw) · [asistenți personali open-source 2026](https://www.vellum.ai/blog/best-open-source-personal-ai-assistants)
- Agenți/SDK: [Claude Agent SDK 2026](https://www.totalum.app/blog/claude-agent-sdk-totalum-2026) · [subagenți](https://www.totalum.app/blog/claude-code-subagents-totalum) · [A2A la un an](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year)
- Oportunități: [Anthropic Economic Futures](https://www.anthropic.com/economic-futures/program) · [Anthropic Fellows](https://alignment.anthropic.com/2025/anthropic-fellows-program-2026/) · [Claude Corps](https://www.anthropic.com/news/claude-corps) · [Ruya AI Self-Improving Agents](https://ruyaai-hackathon-2026.devpost.com/) · [The Bucharest Hackathon](https://thebucharesthackathon.com/) · [European AI Hackathon](https://www.openhackathons.org/s/siteevent/a0CUP00003yKxcX2AS/se000475) · [HackEurope](https://hackeurope.devpost.com/)
