import type {
  ArtifactItem,
  DataRoleConfig,
  ForecastItem,
  OptimisationDetail,
  RunDetail,
  RunSummaryItem,
  ScenarioDetail,
  Section,
  SectionIndexItem,
} from "./types";

const ACCESS_CODE_STORAGE_KEY = "mmm_studio_access_code";

type AuthPromptListener = () => void;
const authListeners = new Set<AuthPromptListener>();

export function onAuthRequired(listener: AuthPromptListener): () => void {
  authListeners.add(listener);
  return () => {
    authListeners.delete(listener);
  };
}

export function getAccessCode(): string {
  return sessionStorage.getItem(ACCESS_CODE_STORAGE_KEY) || "";
}

export function setAccessCode(code: string): void {
  if (code) {
    sessionStorage.setItem(ACCESS_CODE_STORAGE_KEY, code);
  } else {
    sessionStorage.removeItem(ACCESS_CODE_STORAGE_KEY);
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers || {});
  const code = getAccessCode();
  if (code) {
    headers.set("X-Access-Code", code);
  }
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const resp = await fetch(path, {
    ...init,
    headers,
  });

  if (resp.status === 401 || resp.status === 403) {
    authListeners.forEach((fn) => fn());
  }

  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const payload = await resp.json();
      detail = payload.detail || JSON.stringify(payload);
    } catch {
      // ignore json parse failure
    }
    throw new ApiError(resp.status, detail);
  }

  return (await resp.json()) as T;
}

export const api = {
  checkAuth: () => apiFetch<{ ok: boolean; protected: boolean }>("/api/v1/auth/check", { method: "POST" }),

  listRuns: () => apiFetch<{ runs: RunSummaryItem[]; active_run_id: string | null }>("/api/v1/runs"),

  getRun: (runId: string) => apiFetch<RunDetail>(`/api/v1/runs/${runId}`),

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
      profile?: "quick" | "full" | "smoke";
      role_config?: DataRoleConfig;
      skip_meridian?: boolean;
    }
  ) =>
    apiFetch<RunDetail>(`/api/v1/runs/${runId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  startRun: (
    runId: string,
    payload: { profile?: "quick" | "full" | "smoke"; skip_meridian?: boolean; seed?: number }
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
