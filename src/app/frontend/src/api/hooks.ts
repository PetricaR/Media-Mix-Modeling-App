import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export function useRuns() {
  return useQuery({
    queryKey: ["runs"],
    queryFn: () => api.listRuns(),
    refetchInterval: 5000,
  });
}

export function useRun(runId: string | null) {
  return useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId!),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 2000 : false;
    },
  });
}

export function useSections(runId: string | null) {
  return useQuery({
    queryKey: ["sections", runId],
    queryFn: () => api.listSections(runId!),
    enabled: Boolean(runId),
    refetchInterval: 4000,
  });
}

export function useSection(runId: string | null, sectionId: string | null) {
  return useQuery({
    queryKey: ["section", runId, sectionId],
    queryFn: () => api.getSection(runId!, sectionId!),
    enabled: Boolean(runId && sectionId),
    retry: 1,
  });
}

export function useForecasts(runId: string | null) {
  return useQuery({
    queryKey: ["forecasts", runId],
    queryFn: () => api.listForecasts(runId!),
    enabled: Boolean(runId),
  });
}

export function useForecast(forecastId: string | null) {
  return useQuery({
    queryKey: ["forecast", forecastId],
    queryFn: () => api.getForecast(forecastId!),
    enabled: Boolean(forecastId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 2000 : false;
    },
  });
}

export function useScenario(scenarioId: string | null) {
  return useQuery({
    queryKey: ["scenario", scenarioId],
    queryFn: () => api.getScenario(scenarioId!),
    enabled: Boolean(scenarioId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 1000 : false;
    },
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
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 1000 : false;
    },
  });
}

export function useArtifacts(runId: string | null) {
  return useQuery({
    queryKey: ["artifacts", runId],
    queryFn: () => api.listArtifacts(runId!),
    enabled: Boolean(runId),
  });
}
