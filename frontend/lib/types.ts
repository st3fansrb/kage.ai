// Forma stării Mission Control, așa cum o emite backend-ul în STATE_SNAPSHOT (/agui).
// Vezi orchestrator.py::_mc_state.

export type AgentStatus = "running" | "pending" | "done" | "failed";

export interface Budget {
  cloudCalls: number;
  maxCloud: number;
  spentUsd: number;
  spentEur: number | null;
  maxUsd: number;
}

export interface Agent {
  id: string;
  name: string;
  status: AgentStatus;
  note: string;
  elapsedMs: number | null;
  costUsd: number | null;
}

export interface Approval {
  id: string;
  agent: string;
  risk: string;
  cmd: string;
  why: string;
  time: string;
}

export interface ActivityRow {
  id: string;
  ts: string;
  tier: string;
  channel: string;
  text: string;
  meta: string;
  status: AgentStatus;
  costUsd: number | null;
}

export interface Cache {
  hits: number;
  misses: number;
  hitRate: number | null;
  entries: number;
  memories: number;
  routingExamples: number;
}

export interface MissionState {
  budget: Budget;
  agents: Agent[];
  approvals: Approval[];
  activity: ActivityRow[];
  cache: Cache;
  runningCount: number;
}

export const EMPTY_STATE: MissionState = {
  budget: { cloudCalls: 0, maxCloud: 20, spentUsd: 0, spentEur: 0, maxUsd: 5 },
  agents: [],
  approvals: [],
  activity: [],
  cache: { hits: 0, misses: 0, hitRate: null, entries: 0, memories: 0, routingExamples: 0 },
  runningCount: 0,
};
