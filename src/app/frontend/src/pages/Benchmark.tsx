import React from "react";
import { useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";

export const BenchmarkPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: section, isLoading, error } = useSection(runId, "benchmark");
  return (
    <SectionRenderer
      section={section}
      isLoading={isLoading}
      error={error as Error | null}
      pendingMessage="The OLS benchmark appears when the analysis stages have finished; Meridian's row is added after its fit."
    />
  );
};
