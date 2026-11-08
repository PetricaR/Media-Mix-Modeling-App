import React from "react";
import { Button } from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import { useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";
import { CURATIONS } from "../components/Sections/curations";

export const PreparationPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: section, isLoading, error } = useSection(runId, "preparation");

  return (
    <SectionRenderer
      section={section}
      isLoading={isLoading}
      error={error as Error | null}
      curation={CURATIONS.preparation}
      headerActions={
        runId && section ? (
          <>
            <Button size="small" variant="outlined" startIcon={<DownloadIcon fontSize="small" />} href={`/api/v1/runs/${runId}/artifacts/data/wide_model.csv?download=true`}>
              Wide daily table (CSV)
            </Button>
            <Button size="small" variant="outlined" startIcon={<DownloadIcon fontSize="small" />} href={`/api/v1/runs/${runId}/artifacts/data/meridian_input.csv?download=true`}>
              Meridian input table (CSV)
            </Button>
          </>
        ) : undefined
      }
    />
  );
};
