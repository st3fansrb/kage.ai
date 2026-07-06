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

export interface MissionState {
  budget: Budget;
  agents: Agent[];
  approvals: Approval[];
  activity: ActivityRow[];
  runningCount: number;
}

export const EMPTY_STATE: MissionState = {
  budget: { cloudCalls: 0, maxCloud: 20, spentUsd: 0, spentEur: 0, maxUsd: 5 },
  agents: [],
  approvals: [],
  activity: [],
  runningCount: 0,
};
