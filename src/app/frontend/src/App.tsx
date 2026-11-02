import React, { useCallback, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/Layout/AppShell";
import { ImportPage } from "./pages/Import";
import { OverviewPage } from "./pages/Overview";
import { DataQualityPage } from "./pages/DataQuality";
import { ExplorePage } from "./pages/Explore";
import { PreparationPage } from "./pages/Preparation";
import { ModelPage } from "./pages/Model";
import { DriversPage } from "./pages/Drivers";
import { BenchmarkPage } from "./pages/Benchmark";
import { OptimizerPage } from "./pages/Optimizer";
import { ForecastPage } from "./pages/Forecast";
import { ReportsPage } from "./pages/Reports";
import { BriefPage } from "./pages/Brief";

const RUN_STORAGE_KEY = "mmm_studio_selected_run";

function readStoredRun(): string | null {
  try {
    return localStorage.getItem(RUN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export const App: React.FC = () => {
  // The selected run survives a page reload; AppShell falls back to the default run if it no longer exists
  const [selectedRunId, setSelected] = useState<string | null>(readStoredRun);
  const setSelectedRunId = useCallback((id: string | null) => {
    setSelected(id);
    try {
      if (id) localStorage.setItem(RUN_STORAGE_KEY, id);
      else localStorage.removeItem(RUN_STORAGE_KEY);
    } catch {
      // storage unavailable (private mode): selection simply is not remembered
    }
  }, []);

  return (
    <AppShell selectedRunId={selectedRunId} onSelectRunId={setSelectedRunId}>
      <Routes>
        <Route path="/" element={<Navigate to="/overview" replace />} />
        <Route
          path="/import"
          element={
            <ImportPage selectedRunId={selectedRunId} onSelectRunId={setSelectedRunId} />
          }
        />
        <Route path="/overview" element={<OverviewPage runId={selectedRunId} />} />
        <Route path="/data-quality" element={<DataQualityPage runId={selectedRunId} />} />
        <Route path="/explore" element={<ExplorePage runId={selectedRunId} />} />
        <Route path="/preparation" element={<PreparationPage runId={selectedRunId} />} />
        <Route path="/model" element={<ModelPage runId={selectedRunId} />} />
        <Route path="/drivers" element={<DriversPage runId={selectedRunId} />} />
        <Route path="/benchmark" element={<BenchmarkPage runId={selectedRunId} />} />
        <Route path="/optimizer" element={<OptimizerPage runId={selectedRunId} />} />
        <Route path="/forecast" element={<ForecastPage runId={selectedRunId} />} />
        <Route path="/reports" element={<ReportsPage runId={selectedRunId} />} />
        <Route path="/brief" element={<BriefPage runId={selectedRunId} />} />
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Routes>
    </AppShell>
  );
};

export default App;
