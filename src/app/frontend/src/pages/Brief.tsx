import React from "react";
import { useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";

export const BriefPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: section, isLoading, error } = useSection(runId, "brief");
  return (
    <SectionRenderer
      section={section}
      isLoading={isLoading}
      error={error as Error | null}
      pendingMessage="The answers to the brief appear when the analysis stages have finished and are updated with the Meridian results."
    />
  );
};
