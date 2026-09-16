const TOKEN_KEY = "aegis_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(path, { ...init, headers });
  if (res.status === 401) {
    clearToken();
    if (!path.includes("/auth/login")) {
      window.location.href = "/login";
    }
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// Phase 19 — the Gmail connection, as the operator sees it.
//
// Note what is absent from GmailConnection: there is no token field, because
// no endpoint returns one. The dashboard shows which mailbox is connected and
// what was granted; the credential itself never leaves the server.
export type GmailConnection = {
  organization_id: string;
  google_email: string;
  scopes: string[];
  connected_at: string;
  connected_by: string | null;
  status: string;
};

export type GmailStatus = {
  oauth_client_configured: boolean;
  encryption_key_configured: boolean;
  connected: boolean;
  connection: GmailConnection | null;
  requested_scopes: string[];
  // Phase 19.1: connecting a mailbox gives no agent access to it. This is who
  // actually has it, so an operator is never guessing.
  agents_with_access: Array<{
    agent_id: string;
    agent_name: string;
    granted_at: string | null;
  }>;
};

export type AgentGmailStatus = {
  agent_id: string;
  agent_name: string;
  agent_status: string;
  connected: boolean;
  granted: boolean;
  allowed: boolean;
  reason: string;
  google_email: string | null;
};

export type AgentSetup = {
  agent_id: string;
  agent_name: string;
  status: string;
  gateway_base_url_env: string;
  gateway_path_pattern: string;
  auth_header: string;
  request_shape: Record<string, unknown>;
  operations: Array<{ canonical: string; path: string; scope: string }>;
  credential: { shown_once: boolean; note: string };
  never_supplied_to_agents: string[];
};

export type ConnectorCall = {
  id: string;
  created_at: string | null;
  agent_id: string | null;
  request_id: string | null;
  tool: string;
  requested_operation: string;
  canonical_operation: string;
  declared_intent_untrusted: string | null;
  decision: string;
  approval_id: string | null;
  approval_granted: boolean;
  executed: boolean;
  connector_operation: string | null;
  result_status: string;
  resource_ref: string | null;
  error_code: string | null;
  intent_matches_operation: boolean | null;
};

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  register: (payload: {
    organization_name: string;
    full_name: string;
    email: string;
    password: string;
  }) =>
    request<{ access_token: string }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  me: () => request<{ user: User; organization: Org }>("/api/auth/me"),
  gmailStatus: () => request<GmailStatus>("/api/gmail/status"),
  gmailConnect: () =>
    request<{ authorization_url: string; expires_in: number }>(
      "/api/gmail/oauth/start",
      { method: "POST" },
    ),
  gmailDisconnect: () =>
    request<{ disconnected: boolean; agent_grants_revoked: number; note: string }>(
      "/api/gmail/disconnect",
      { method: "POST" },
    ),
  agentGmail: (agentId: string) =>
    request<AgentGmailStatus>(`/api/gmail/agents/${agentId}`),
  grantAgentGmail: (agentId: string) =>
    request<{ agent_id: string; granted: boolean; google_email: string | null }>(
      `/api/gmail/agents/${agentId}/grant`,
      { method: "POST" },
    ),
  revokeAgentGmail: (agentId: string) =>
    request<{ agent_id: string; granted: boolean; revoked: boolean }>(
      `/api/gmail/agents/${agentId}/revoke`,
      { method: "POST" },
    ),
  agentSetup: (agentId: string) => request<AgentSetup>(`/api/agents/${agentId}/setup`),
  connectorCalls: (executionId: string) =>
    request<{ execution_id: string; count: number; note: string; calls: ConnectorCall[] }>(
      `/api/executions/${executionId}/connector-calls`,
    ),
  stats: () => request<Stats>("/api/stats"),
  agents: () => request<Agent[]>("/api/agents"),
  createAgent: (body: { name: string; provider: string; model: string; description: string }) =>
    request<{ agent: Agent; token: string }>("/api/agents", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  revokeAgent: (id: string) =>
    request<Agent>(`/api/agents/${id}/revoke`, { method: "POST" }),
  rotateAgent: (id: string) =>
    request<{ agent: Agent; token: string }>(`/api/agents/${id}/rotate`, {
      method: "POST",
    }),
  permissions: (id: string) => request<Permission[]>(`/api/agents/${id}/permissions`),
  addPermission: (id: string, body: PermissionCreate) =>
    request<Permission>(`/api/agents/${id}/permissions`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  policies: () => request<Policy[]>("/api/policies"),
  createPolicy: (body: PolicyCreate) =>
    request<Policy>("/api/policies", { method: "POST", body: JSON.stringify(body) }),
  togglePolicy: (id: string) =>
    request<Policy>(`/api/policies/${id}/toggle`, { method: "POST" }),
  events: () => request<EventRow[]>("/api/events"),
  alerts: () => request<AlertRow[]>("/api/alerts"),
  approvals: () => request<ApprovalRow[]>("/api/approvals"),
  decide: (id: string, decision: string) =>
    request<ApprovalRow>(`/api/approvals/${id}/decide`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    }),
  contracts: (agentId: string) =>
    request<RuntimeContract[]>(`/api/agents/${agentId}/contracts`),
  activeContract: (agentId: string) =>
    request<RuntimeContract>(`/api/agents/${agentId}/contracts/active`),
  setContractStatus: (agentId: string, contractId: string, version: number, status: string) =>
    request<RuntimeContract>(
      `/api/agents/${agentId}/contracts/${contractId}/${version}/status`,
      { method: "POST", body: JSON.stringify({ status }) },
    ),
  agent: (id: string) => request<Agent>(`/api/agents/${id}`),
  createContract: (agentId: string, doc: ContractDraft) =>
    request<RuntimeContract>(`/api/agents/${agentId}/contracts`, {
      method: "POST",
      body: JSON.stringify(doc),
    }),
  capabilities: () => request<CapabilityCatalogue>("/api/capabilities"),
  simulate: (agentId: string, body: SimulateBody) =>
    request<SimulationResult>(`/api/agents/${agentId}/simulate`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  scenarios: () => request<Scenario[]>("/api/verification/scenarios"),
  requestRun: (agentId: string, scenario: string) =>
    request<VerificationRun>(`/api/agents/${agentId}/verification-runs`, {
      method: "POST",
      body: JSON.stringify({ scenario }),
    }),
  runs: (agentId?: string) =>
    request<VerificationRun[]>(
      agentId ? `/api/verification-runs?agent_id=${agentId}` : "/api/verification-runs",
    ),
  run: (runId: string) => request<VerificationRun>(`/api/verification-runs/${runId}`),
  cancelRun: (runId: string) =>
    request<VerificationRun>(`/api/verification-runs/${runId}/cancel`, { method: "POST" }),
  health: () => request<HealthReport>("/api/health"),
  executions: () => request<ExecutionRow[]>("/api/executions"),
  evidence: (executionId: string) =>
    request<EvidenceReport>(`/api/executions/${executionId}/evidence`),
  resources: () => request<ResourceRow[]>("/api/resources"),
  devices: () => request<DeviceRow[]>("/api/devices"),
};

export type User = {
  id: string;
  email: string;
  full_name: string;
  role: string;
  organization_id: string;
};
export type Org = { id: string; name: string; slug: string; created_at: string };
export type Stats = {
  agents: number;
  events: number;
  blocked: number;
  pending_approvals: number;
  open_alerts: number;
  allow_rate: number;
};
export type Agent = {
  id: string;
  name: string;
  provider: string;
  model: string;
  status: string;
  description: string;
  owner_id: string;
  organization_id: string;
  created_at: string;
  revoked_at: string | null;
};
export type Permission = {
  id: string;
  agent_id: string;
  resource_kind: string;
  action: string;
  scope: string;
  effect: string;
};
export type PermissionCreate = {
  resource_kind: string;
  action: string;
  scope: string;
  effect: string;
};
export type Policy = {
  id: string;
  name: string;
  description: string;
  resource_kind: string;
  action: string;
  scope_pattern: string;
  destination_pattern: string | null;
  decision: string;
  priority: number;
  enabled: boolean;
  created_at: string;
};
export type PolicyCreate = {
  name: string;
  description: string;
  resource_kind: string;
  action: string;
  scope_pattern: string;
  destination_pattern?: string;
  decision: string;
  priority: number;
};
export type EventRow = {
  id: string;
  agent_id: string | null;
  seq: number;
  resource_kind: string;
  action: string;
  scope: string;
  destination: string | null;
  decision: string;
  risk_score: number;
  risk_level: string;
  reason: string;
  request_id: string;
  execution_id?: string | null;
  created_at: string;
};
export type AlertRow = {
  id: string;
  severity: string;
  title: string;
  message: string;
  status: string;
  created_at: string;
};
export type ApprovalRow = {
  id: string;
  agent_id: string;
  resource_kind: string;
  action: string;
  scope: string;
  destination: string | null;
  status: string;
  reason: string;
  created_at: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  execution_id: string | null;
  request_id: string | null;
  contract_id: string | null;
  contract_version: number | null;
  param_hash: string | null;
  expires_at: string | null;
  consumed_at: string | null;
  consumed_event_id: string | null;
};
export type ResourceRow = {
  id: string;
  kind: string;
  name: string;
  identifier: string;
  sensitivity: string;
};
export type DeviceRow = {
  id: string;
  hostname: string;
  platform: string;
  status: string;
  last_seen: string;
};

export type RuntimeContract = {
  id: string;
  organization_id: string;
  agent_id: string;
  contract_id: string;
  version: number;
  status: string;
  purpose: string;
  capabilities: Array<Record<string, unknown>>;
  resources: Array<Record<string, unknown>>;
  constraints: Record<string, unknown>;
  data_constraints: Record<string, unknown>;
  workflow: Record<string, unknown> | null;
  approval_rules: Array<Record<string, unknown>>;
  valid_from: string | null;
  expires_at: string | null;
  created_at: string;
};
export type ExecutionRow = {
  id: string;
  agent_id: string;
  created_at: string;
  evidence_chain_tip: string | null;
  event_count: number;
};
export type EvidenceEvent = {
  event_id: string;
  seq: number;
  request_id: string;
  resource_kind: string;
  action: string;
  scope: string;
  destination: string | null;
  decision: string;
  reason: string;
  payload_hash: string | null;
  previous_evidence_hash: string | null;
  evidence_hash: string | null;
  recomputed_evidence_hash: string | null;
  digest_matches: boolean | null;
  created_at: string;
};
export type EvidenceApproval = {
  id: string;
  status: string;
  resource_kind: string;
  action: string;
  scope: string;
  contract_id: string | null;
  contract_version: number | null;
  param_hash: string | null;
  reviewed_by: string | null;
  consumed_at: string | null;
  consumed_event_id: string | null;
};
export type EvidenceReport = {
  execution: {
    id: string;
    agent_id: string;
    created_at: string;
    evidence_chain_tip: string | null;
  };
  verdict: { valid: boolean; reason: string | null; first_bad_event: string | null };
  event_count: number;
  chain: EvidenceEvent[];
  approvals: EvidenceApproval[];
};

export type Capability = {
  resource_kind: string;
  action: string;
  enforceable: boolean;
  irreversible: boolean;
  registered_resource: boolean;
  default_scope: string;
};
export type CapabilityCatalogue = {
  capabilities: Capability[];
  executable_tools: string[];
  note: string;
};
export type ContractDraft = {
  organization_id: string;
  agent_id: string;
  contract_id: string;
  version: number;
  status: string;
  purpose: string;
  capabilities: Array<Record<string, unknown>>;
  resources: Array<Record<string, unknown>>;
  constraints: Record<string, unknown>;
  data_constraints: Record<string, unknown>;
  approval_rules: Array<Record<string, unknown>>;
};
export type Scenario = { id: string; description: string };
export type VerificationRun = {
  id: string;
  agent_id: string;
  scenario: string;
  status: string;
  execution_id: string | null;
  result: Record<string, any> | null;
  error: string | null;
  created_at: string | null;
  claimed_at: string | null;
  finished_at: string | null;
};
export type HealthReport = {
  status: string;
  product: string;
  layer: string;
  posture: {
    role: string;
    default_secrets_allowed: boolean;
    weak_secrets: string[];
    secure: boolean;
  };
};

export type SimulateBody = {
  resource_kind: string;
  action: string;
  scope: string;
  destination?: string | null;
  payload?: Record<string, unknown> | null;
};
export type SimulationStep = { layer: string; outcome: string; detail: string };
export type SimulationResult = {
  decision: string;
  reason: string;
  risk_level: string;
  risk_score: number;
  contract_id: string | null;
  contract_version: number | null;
  steps: SimulationStep[];
  models_trajectory: boolean;
  advisory: boolean;
  note?: string;
};
