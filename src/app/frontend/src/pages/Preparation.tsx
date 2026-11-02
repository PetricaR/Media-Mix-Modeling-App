import React from "react";
import { Box, Button, Stack } from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import { useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";

export const PreparationPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: section, isLoading, error } = useSection(runId, "preparation");

  return (
    <Box>
      {runId && section && (
        <Stack direction="row" spacing={1.5} justifyContent="flex-end" flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
          <Button size="small" variant="outlined" startIcon={<DownloadIcon fontSize="small" />} href={`/api/v1/runs/${runId}/artifacts/data/wide_model.csv?download=true`}>
            Wide daily table (CSV)
          </Button>
          <Button size="small" variant="outlined" startIcon={<DownloadIcon fontSize="small" />} href={`/api/v1/runs/${runId}/artifacts/data/meridian_input.csv?download=true`}>
            Meridian input table (CSV)
          </Button>
        </Stack>
      )}
      <SectionRenderer section={section} isLoading={isLoading} error={error as Error | null} />
    </Box>
  );
};
