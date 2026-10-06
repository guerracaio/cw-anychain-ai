export interface Network {
  id: string;
  name: string;
  chain_id: number;
  native_currency: string;
  native_decimals: number;
}

export type AnalysisMode = "developer" | "support" | "auditor";

export interface AnalyzeRequest {
  tx_hash: string;
  mode: AnalysisMode;
}

export interface ExplanationFinding {
  kind: "observed" | "decoded" | "state" | "source" | "inference";
  statement: string;
  evidence_ids: string[];
}

export interface Explanation {
  summary: string;
  findings: ExplanationFinding[];
  likely_causes: Finding[];
  next_steps: string[];
  security_notes: Finding[];
  uncertainties: string[];
  discarded_items: number;
  unverified_identifiers: string[];
  usage: {
    provider: string;
    model: string;
    steps: number;
    tool_calls: string[];
    input_tokens: number;
    output_tokens: number;
    thinking_tokens: number;
    estimated_cost_usd: number | null;
    duration_ms: number;
  };
}

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export interface Evidence {
  id: string;
  source_type: "blockscout" | "rpc" | "repository" | "documentation" | "decoder";
  source_url: string | null;
  description: string;
  payload: JsonValue;
  confidence: "observed" | "decoded" | "inferred";
}

export interface Finding {
  description: string;
  evidence_ids: string[];
}

// Contract source is untrusted text: render it only as escaped text, never as HTML.
export interface SourceExcerpt {
  origin: "explorer" | "repository";
  contract: string;
  address: string | null;
  symbol: string;
  kind: string;
  reason: string;
  path: string;
  start_line: number;
  end_line: number;
  code: string;
  truncated: boolean;
  repository: string | null;
  commit: string | null;
  url: string | null;
  evidence_ids: string[];
}

export interface DecodedArg {
  name: string;
  type: string;
  value: JsonValue;
  hashed: boolean;
  truncated: boolean;
}

export interface NestedCall {
  argument: string;
  index: number;
  selector: string | null;
  function: string | null;
  signature: string | null;
  arguments: DecodedArg[];
  reason: string | null;
}

export interface DecodedCall {
  function: string;
  signature: string;
  selector: string;
  arguments: DecodedArg[];
  abi_source: "explorer" | "repository";
  contract: string;
  abi_address: string;
  evidence_ids: string[];
  nested_calls: NestedCall[];
}

export interface DecodedEvent {
  name: string;
  signature: string;
  arguments: DecodedArg[];
  abi_source: "explorer" | "repository";
  abi_address: string;
  evidence_ids: string[];
}

export interface RevertInfo {
  source: "rpc_replay" | "explorer";
  kind: "error_string" | "panic" | "custom_error" | "empty" | "unknown" | "out_of_gas";
  signature: string | null;
  name: string | null;
  message: string | null;
  arguments: DecodedArg[];
  raw: string | null;
  abi_address: string | null;
  ambiguous: boolean;
  evidence_ids: string[];
}

export interface StateRead {
  contract: string;
  signature: string;
  arguments: string[];
  block: string;
  outputs: DecodedArg[];
  evidence_ids: string[];
}

export interface Diagnosis {
  confirmed: Finding[];
  likely_causes: Finding[];
  next_steps: string[];
  revert: RevertInfo | null;
  replay: "reverted" | "succeeded" | "unavailable" | "not_attempted" | null;
  state_reads: StateRead[];
}

export interface TransactionDetails {
  sender: string | null;
  recipient: string | null;
  recipient_is_contract: boolean | null;
  created_contract: string | null;
  block_number: number | null;
  block_hash: string | null;
  value_raw: string | null;
  value_display: string | null;
  native_currency: string;
  calldata: string | null;
  selector: string | null;
  gas_limit: string | null;
  gas_used: string | null;
  gas_price: string | null;
  decoded_input: DecodedCall | null;
  field_sources: Record<string, string[]>;
}

export interface RawLog {
  address: string;
  index: number | null;
  topics: string[];
  data: string;
  decoded: DecodedEvent | null;
  evidence_ids: string[];
}

export interface IndexedTransfer {
  sender: string | null;
  recipient: string | null;
  token_address: string | null;
  token_symbol: string | null;
  token_type: string | null;
  total: JsonValue;
  evidence_ids: string[];
}

export interface InternalCall {
  sender: string | null;
  recipient: string | null;
  call_type: string | null;
  success: boolean | null;
  value_raw: string | null;
  evidence_ids: string[];
}

// Keep synchronized with backend/app/domain/analysis.py and transaction.py.
export interface AnalysisResponse {
  network: string;
  tx_hash: string;
  status: "success" | "failed" | "pending" | "unknown";
  summary: string;
  request_id: string;
  explorer_url: string;
  transaction: TransactionDetails;
  status_evidence_ids: string[];
  calls: InternalCall[];
  transfers: IndexedTransfer[];
  events: RawLog[];
  diagnosis: Diagnosis;
  contract_context: SourceExcerpt[];
  security_notes: Finding[];
  sources: Evidence[];
  uncertainties: string[];
  mode: AnalysisMode;
  explanation: Explanation | null;
  explanation_issue: string | null;
}

export function isTransactionHash(value: string): boolean {
  return /^0x[0-9a-fA-F]{64}$/.test(value) && value.length === 66;
}

export async function getNetwork(signal: AbortSignal): Promise<Network> {
  const response = await fetch("/api/network", { signal, cache: "no-store" });
  if (!response.ok) throw new Error("Não foi possível consultar a rede configurada.");
  return response.json();
}

export async function submitAnalysis(request: AnalyzeRequest, signal?: AbortSignal): Promise<AnalysisResponse> {
  const timeout = AbortSignal.timeout(135_000);
  const response = await fetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  if (response.status === 422) throw new Error("Confira o hash da transação e tente novamente.");
  if (!response.ok) {
    const requestId = response.headers.get("X-Request-ID");
    throw new Error(`O serviço não conseguiu atender à solicitação. Tente novamente.${requestId ? ` (request_id ${requestId})` : ""}`);
  }
  return response.json();
}

// Settings ---------------------------------------------------------------------------------

export type SecretName = "rpc_url" | "github_token" | "llm_api_key";

export interface SecretStatus {
  configured: boolean;
  source: "file" | "env" | null;
  env_var: string | null;
}

export interface ProfileSummary {
  id: string;
  name: string;
  chain_id: number | null;
  active: boolean;
  valid: boolean;
}

export interface RepositorySettings {
  url: string;
  branch: string;
  contracts: { address: string; name: string }[];
}

export interface ProfileSettings {
  id: string;
  network: { name: string | null; chain_id: number | null; native_currency: string | null; native_decimals: number | null };
  explorer: { base_url: string | null };
  repositories: RepositorySettings[];
  llm: {
    provider: string | null;
    model: string | null;
    thinking_level: string | null;
    input_price_per_million: number | null;
    output_price_per_million: number | null;
  };
  secrets: Record<SecretName, SecretStatus>;
  env_bound: Record<string, string>;
}

export interface SecretChange {
  action: "keep" | "set" | "clear" | "env";
  value?: string;
}

export interface ProfileUpdate {
  network: ProfileSettings["network"];
  explorer: ProfileSettings["explorer"];
  repositories: RepositorySettings[];
  llm: ProfileSettings["llm"];
  secrets: Partial<Record<SecretName, SecretChange>>;
  copy_from?: string | null;
}

export interface SettingsStatus {
  available: boolean;
  writable: boolean;
  admin_required: boolean;
  active: string | null;
}

export interface ConnectionCheck {
  id: string;
  label: string;
  ok: boolean | null;
  code: string;
}

export class SettingsRequestError extends Error {
  constructor(public status: number, public code: string, message: string, public fields: string[] = []) {
    super(message);
  }
}

const ADMIN_KEY = "anychain.adminToken";

export function getAdminToken(): string {
  try { return sessionStorage.getItem(ADMIN_KEY) ?? ""; } catch { return ""; }
}

export function setAdminToken(token: string) {
  try { if (token) sessionStorage.setItem(ADMIN_KEY, token); else sessionStorage.removeItem(ADMIN_KEY); } catch { /* storage unavailable */ }
}

async function settingsRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getAdminToken();
  const response = await fetch(`/api/settings${path}`, {
    ...init,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(token ? { "X-Admin-Token": token } : {}), ...init.headers },
  });
  if (!response.ok) {
    let body: { error?: { code?: string; message?: string; fields?: string[] } } = {};
    try { body = await response.json(); } catch { /* non-JSON error */ }
    const error = body.error ?? {};
    throw new SettingsRequestError(response.status, error.code ?? "request_failed",
      error.message ?? "Não foi possível concluir a operação. Tente novamente.", error.fields ?? []);
  }
  return response.json();
}

export const settingsApi = {
  status: () => settingsRequest<SettingsStatus>("/status"),
  profiles: () => settingsRequest<ProfileSummary[]>("/profiles"),
  profile: (id: string) => settingsRequest<ProfileSettings>(`/profiles/${encodeURIComponent(id)}`),
  save: (id: string, body: ProfileUpdate) =>
    settingsRequest<ProfileSettings>(`/profiles/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),
  remove: (id: string) => settingsRequest<{ deleted: string }>(`/profiles/${encodeURIComponent(id)}`, { method: "DELETE" }),
  activate: (id: string) => settingsRequest<{ active: string }>("/active", { method: "POST", body: JSON.stringify({ id }) }),
  test: (id: string) => settingsRequest<{ checks: ConnectionCheck[] }>(`/profiles/${encodeURIComponent(id)}/test`, { method: "POST" }),
};
