import React from "react";
import { useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";
import { CURATIONS } from "../components/Sections/curations";

export const DataQualityPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: section, isLoading, error } = useSection(runId, "data_quality");
  return <SectionRenderer section={section} isLoading={isLoading} error={error as Error | null} curation={CURATIONS.data_quality} />;
};
