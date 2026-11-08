import React from "react";
import { useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";
import { CURATIONS } from "../components/Sections/curations";

export const DriversPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: section, isLoading, error } = useSection(runId, "drivers_roi");
  return (
    <SectionRenderer
      section={section}
      isLoading={isLoading}
      error={error as Error | null}
      curation={CURATIONS.drivers_roi}
      pendingMessage="Channel ROI, contributions, carry-over and response curves appear when the Meridian fit has finished."
    />
  );
};
