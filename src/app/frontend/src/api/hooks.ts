import { useQueries, useQuery } from "@tanstack/react-query";
import { ApiError, api } from "./client";
import type { RunDetail } from "./types";

const ACTIVE = new Set(["queued", "running"]);

export function isRunActive(run?: Pick<RunDetail, "status"> | null): boolean {
  return Boolean(run && ACTIVE.has(run.status));
}

const notFound = (err: unknown) => err instanceof ApiError && err.status === 404;

export function useRuns() {
  return useQuery({
    queryKey: ["runs"],
    queryFn: () => api.listRuns(),
    refetchInterval: (query) => (query.state.data?.runs?.some((r) => ACTIVE.has(r.status)) ? 5000 : 30000),
  });
}

export function useRun(runId: string | null) {
  return useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId!),
    enabled: Boolean(runId),
    retry: (count, err) => !notFound(err) && count < 2,
    refetchInterval: (query) => (isRunActive(query.state.data) ? 2000 : false),
  });
}

/** Server configuration (version, whether writes need an access code); it only changes with a deploy. */
export function useHealth() {
  return useQuery({ queryKey: ["health"], queryFn: () => api.health(), staleTime: Infinity, retry: false });
}

/** The run settings' defaults and limits; they only change with a new API version. */
export function useSettingsDefaults() {
  return useQuery({
    queryKey: ["settings-defaults"],
    queryFn: () => api.getSettingsDefaults(),
    staleTime: Infinity,
  });
}

export function useSections(runId: string | null) {
  const { data: run } = useRun(runId);
  return useQuery({
    queryKey: ["sections", runId],
    queryFn: () => api.listSections(runId!),
    enabled: Boolean(runId),
    refetchInterval: isRunActive(run) ? 4000 : false,
  });
}

/**
 * A rendered section. While the run is queued or running, sections are polled every 4 s: stages publish a section
 * as soon as it exists and later stages rewrite some of them (overview, benchmark, brief) with the Meridian numbers.
 */
export function useSection(runId: string | null, sectionId: string | null) {
  const { data: run } = useRun(runId);
  return useQuery({
    queryKey: ["section", runId, sectionId],
    queryFn: () => api.getSection(runId!, sectionId!),
    enabled: Boolean(runId && sectionId),
    retry: (count, err) => !notFound(err) && count < 2,
    refetchInterval: isRunActive(run) ? 4000 : false,
  });
}

/** The run's daily sales and paid spend, shown before a forecast; it only changes when the run is re-run. */
export function useRunHistory(runId: string | null) {
  return useQuery({
    queryKey: ["history", runId],
    queryFn: () => api.getHistory(runId!),
    enabled: Boolean(runId),
    retry: (count, err) => !notFound(err) && !(err instanceof ApiError && err.status === 409) && count < 2,
    staleTime: 5 * 60_000,
  });
}

export function useForecasts(runId: string | null) {
  return useQuery({
    queryKey: ["forecasts", runId],
    queryFn: () => api.listForecasts(runId!),
    enabled: Boolean(runId),
    refetchInterval: (query) =>
      query.state.data?.forecasts?.some((f) => ACTIVE.has(f.status)) ? 3000 : false,
  });
}

export function useForecast(forecastId: string | null) {
  return useQuery({
    queryKey: ["forecast", forecastId],
    queryFn: () => api.getForecast(forecastId!),
    enabled: Boolean(forecastId),
    retry: (count, err) => !notFound(err) && count < 2,
    refetchInterval: (query) => (ACTIVE.has(query.state.data?.status ?? "") ? 2000 : false),
  });
}

export function useScenario(scenarioId: string | null) {
  return useQuery({
    queryKey: ["scenario", scenarioId],
    queryFn: () => api.getScenario(scenarioId!),
    enabled: Boolean(scenarioId),
    refetchInterval: (query) => (ACTIVE.has(query.state.data?.status ?? "") ? 1000 : false),
  });
}

/** Several finished scenarios at once (the saved scenarios drawn on the forecast chart). */
export function useScenarios(scenarioIds: string[]) {
  return useQueries({
    queries: scenarioIds.map((id) => ({
      queryKey: ["scenario", id],
      queryFn: () => api.getScenario(id),
      retry: (count: number, err: unknown) => !notFound(err) && count < 2,
      refetchInterval: (query: { state: { data?: { status?: string } } }) => (ACTIVE.has(query.state.data?.status ?? "") ? 1000 : false),
    })),
  });
}

export function useOptimisations(runId: string | null) {
  return useQuery({
    queryKey: ["optimisations", runId],
    queryFn: () => api.listOptimisations(runId!),
    enabled: Boolean(runId),
  });
}

export function useOptimisation(optId: string | null) {
  return useQuery({
    queryKey: ["optimisation", optId],
    queryFn: () => api.getOptimisation(optId!),
    enabled: Boolean(optId),
    refetchInterval: (query) => (ACTIVE.has(query.state.data?.status ?? "") ? 1000 : false),
  });
}

export function useArtifacts(runId: string | null) {
  const { data: run } = useRun(runId);
  return useQuery({
    queryKey: ["artifacts", runId],
    queryFn: () => api.listArtifacts(runId!),
    enabled: Boolean(runId),
    refetchInterval: isRunActive(run) ? 10000 : false,
  });
}
