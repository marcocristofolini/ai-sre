export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
  }
}

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin", headers: { Accept: "application/json" } };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { ...init.headers, "Content-Type": "application/json" };
  }
  const resp = await fetch(path, init);
  if (!resp.ok) {
    let detail = "";
    try {
      const data = await resp.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* corpo não é JSON */
    }
    if (resp.status === 401 && path !== "/auth/login") onUnauthorized();
    throw new ApiError(resp.status, detail);
  }
  return (await resp.json()) as T;
}

export const api = {
  me: () => request<{ user: string }>("GET", "/auth/me"),
  login: (username: string, password: string) => request<{ user: string }>("POST", "/auth/login", { username, password }),
  logout: () => request<{ ok: boolean }>("POST", "/auth/logout"),
  incidents: () => request<Incident[]>("GET", "/incidents"),
  incident: (id: number) => request<IncidentDetail>("GET", `/incidents/${id}`),
  approve: (id: number) => request<{ ok: boolean }>("POST", `/incidents/${id}/approve`), // sem corpo: o comando vem do RCA salvo
  risk: () => request<RiskRow[]>("GET", "/risk"),
  scoreboard: () => request<ScoreboardResponse>("GET", "/scoreboard"),
  integrations: () => request<Integration[]>("GET", "/integrations"),
};

export type Evidence = { kind: string; ref: string; summary: string };
export type Rca = {
  cause: string | null;
  confidence: number;
  evidence: Evidence[];
  suggested_command: string | null;
  unknown: boolean;
};
export type Incident = {
  id: number;
  alert_name: string;
  labels: Record<string, string>;
  fired_at: string;
  created_at: string;
  status: "investigating" | "done" | "failed";
  error: string | null;
  rca: Rca | null;
};
export type IncidentDetail = Incident & {
  approvals: { user_id: string; command: string }[];
  approvable: boolean;
};
export type RiskRow = { workload: string; state: "aprendendo" | "ativo"; score: number; threshold: number };
export type ScoreboardResponse =
  | { measured: false; reason: "missing" | "invalid" }
  | {
      measured: true;
      correct: number;
      total: number;
      model: string;
      label: string;
      injection_errors: number;
      calibration: { confidence: number; hit_rate: number }[];
    };
export type Integration = { id: string; configured: boolean; detail: string | null };
