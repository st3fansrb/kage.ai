# Kage Mission Control (WP10)

Frontend Next.js pentru Kage, peste stream-ul AG-UI `/agui` al orchestratorului.

## Rulare (dev)

```bash
cp .env.local.example .env.local   # completează KAGE_API_TOKEN dacă backend-ul cere auth
npm install
npm run dev                        # http://localhost:3001
```

Backend-ul (orchestrator FastAPI) trebuie să ruleze pe `:4001` (`./start_all.sh`).

## Arhitectură

- **`app/api/agui/route.ts`** — proxy server-side: deschide `${ORCHESTRATOR_URL}/agui`
  cu `Authorization: Bearer ${KAGE_API_TOKEN}` și streamează SSE-ul înapoi în browser.
  Token-ul rămâne pe server; browser-ul lovește same-origin `/api/agui` (fără CORS).
- **`lib/useMissionState.ts`** — hook care consumă stream-ul AG-UI (`STATE_SNAPSHOT`)
  și expune starea live a Mission Control-ului.
- **`app/page.tsx`** — ecranul „Astăzi”: o propoziție cu ce contează acum, apoi
  Decizii (aprobări) → Agenți → Bugetul zilei + Programat azi → Ultimele cereri.
- **`components/`** — câte o componentă pe secțiune, plus `Sheet` (dialog lateral nativ)
  folosit de Chat (⌘J) și Task nou (⌘K). Stilul e în `app/globals.css` (tokeni pe `:root`):
  portocaliu = bani/cloud, teal = local/gratuit, roșu doar pentru risc și erori.
- **`app/api/ping`** — spune de ce nu avem legătură (token greșit vs. orchestrator oprit).

## Stare (slice 2)

Dashboard read-only, live din run ledger. Panoul de chat CopilotKit (⌘J) și acțiunile
(approve/deny/stop/retry prin `/risk/decision`, `/api/stop`) vin în slice-urile următoare.
