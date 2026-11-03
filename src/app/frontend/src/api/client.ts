import type {
  ArtifactItem,
  AuthSession,
  DataRoleConfig,
  ForecastItem,
  HealthInfo,
  OptimisationDetail,
  Profile,
  RunDetail,
  RunHistory,
  RunSummaryItem,
  ScenarioDetail,
  Section,
  SectionIndexItem,
  SettingsDefaults,
} from "./types";

type AuthPromptListener = () => void;
const authListeners = new Set<AuthPromptListener>();

/** Called whenever the API answers 401: the session is missing or has expired, so the sign-in screen shows. */
export function onAuthRequired(listener: AuthPromptListener): () => void {
  authListeners.add(listener);
  return () => {
    authListeners.delete(listener);
  };
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Every request carries the HttpOnly session cookie (same origin); the access code itself is never stored here. */
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers || {});
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const resp = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });

  if (resp.status === 401) {
    authListeners.forEach((fn) => fn());
  }

  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const payload = await resp.json();
      detail = typeof payload.detail === "string" ? payload.detail : formatValidationDetail(payload.detail) || JSON.stringify(payload);
    } catch {
      // ignore json parse failure
    }
    throw new ApiError(resp.status, detail);
  }

  return (await resp.json()) as T;
}

/** FastAPI's 422 body lists each invalid field; show their messages instead of raw JSON. */
function formatValidationDetail(detail: unknown): string {
  if (!Array.isArray(detail)) return "";
  return detail
    .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg).replace(/^Value error, /, "") : ""))
    .filter(Boolean)
    .join(" ");
}

export const api = {
  health: () => apiFetch<HealthInfo>("/api/v1/health"),

  session: () => apiFetch<AuthSession>("/api/v1/auth/session"),

  login: (accessCode: string) =>
    apiFetch<{ ok: boolean; protected: boolean }>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ access_code: accessCode }),
    }),

  logout: () => apiFetch<{ ok: boolean }>("/api/v1/auth/logout", { method: "POST" }),

  listRuns: () => apiFetch<{ runs: RunSummaryItem[]; active_run_id: string | null }>("/api/v1/runs"),

  getRun: (runId: string) => apiFetch<RunDetail>(`/api/v1/runs/${runId}`),

  getSettingsDefaults: () => apiFetch<SettingsDefaults>("/api/v1/runs/settings/defaults"),

  uploadRun: (file: File, name?: string, profile: string = "quick") => {
    const form = new FormData();
    form.append("file", file);
    if (name) form.append("name", name);
    form.append("profile", profile);
    return apiFetch<RunDetail>("/api/v1/runs", { method: "POST", body: form });
  },

  updateRunConfig: (
    runId: string,
    payload: {
      name?: string;
      profile?: Profile;
      role_config?: DataRoleConfig;
      skip_meridian?: boolean;
      seed?: number;
    }
  ) =>
    apiFetch<RunDetail>(`/api/v1/runs/${runId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  /** Start a draft or re-run a finished run with these settings in one request (nothing changes if it is refused). */
  startRun: (
    runId: string,
    payload: { profile?: Profile; skip_meridian?: boolean; seed?: number; role_config?: DataRoleConfig }
  ) =>
    apiFetch<{ id: string; status: string }>(`/api/v1/runs/${runId}/start`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  cancelRun: (runId: string) =>
    apiFetch<{ id: string; status: string }>(`/api/v1/runs/${runId}/cancel`, {
      method: "POST",
    }),

  activateRun: (runId: string) =>
    apiFetch<{ id: string; is_active: boolean }>(`/api/v1/runs/${runId}/activate`, {
      method: "POST",
    }),

  deleteRun: (runId: string) =>
    apiFetch<{ deleted: string }>(`/api/v1/runs/${runId}`, {
      method: "DELETE",
    }),

  listSections: (runId: string) =>
    apiFetch<{ run_id: string; status: string; sections: SectionIndexItem[] }>(
      `/api/v1/runs/${runId}/sections`
    ),

  getSection: (runId: string, sectionId: string) =>
    apiFetch<Section>(`/api/v1/runs/${runId}/sections/${sectionId}`),

  getHistory: (runId: string) => apiFetch<RunHistory>(`/api/v1/runs/${runId}/history`),

  listForecasts: (runId: string) =>
    apiFetch<{ run_id: string; forecasts: ForecastItem[] }>(`/api/v1/runs/${runId}/forecasts`),

  getForecast: (forecastId: string) => apiFetch<ForecastItem>(`/api/v1/forecasts/${forecastId}`),

  uploadForecastPlan: (runId: string, file: File, name?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (name) form.append("name", name);
    return apiFetch<{ id: string; run_id: string; status: string }>(
      `/api/v1/runs/${runId}/forecasts`,
      { method: "POST", body: form }
    );
  },

  createScenario: (
    forecastId: string,
    payload: { name: string; multipliers: Record<string, number>; horizon_days: number }
  ) =>
    apiFetch<{ id: string; forecast_id: string; status: string }>(
      `/api/v1/forecasts/${forecastId}/scenarios`,
      { method: "POST", body: JSON.stringify(payload) }
    ),

  getScenario: (scenarioId: string) => apiFetch<ScenarioDetail>(`/api/v1/scenarios/${scenarioId}`),

  listOptimisations: (runId: string) =>
    apiFetch<{ run_id: string; optimisations: OptimisationDetail[] }>(
      `/api/v1/runs/${runId}/optimisations`
    ),

  createOptimisation: (
    runId: string,
    payload: {
      name?: string;
      total_budget?: number | null;
      min_multiplier?: number;
      max_multiplier?: number;
      include_catalog?: boolean;
      channel_bounds?: Record<string, [number, number]>;
    }
  ) =>
    apiFetch<{ id: string; run_id: string; status: string }>(
      `/api/v1/runs/${runId}/optimisations`,
      { method: "POST", body: JSON.stringify(payload) }
    ),

  getOptimisation: (optId: string) =>
    apiFetch<OptimisationDetail>(`/api/v1/optimisations/${optId}`),

  listArtifacts: (runId: string) =>
    apiFetch<{ run_id: string; artifacts: ArtifactItem[] }>(`/api/v1/runs/${runId}/artifacts`),
};
