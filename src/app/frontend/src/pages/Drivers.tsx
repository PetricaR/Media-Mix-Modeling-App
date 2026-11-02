import React from "react";
import { useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";

export const DriversPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: section, isLoading, error } = useSection(runId, "drivers_roi");
  return <SectionRenderer section={section} isLoading={isLoading} error={error as Error | null} />;
};
