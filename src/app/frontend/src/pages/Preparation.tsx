import React from "react";
import { Box, Button, Stack } from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import { useSection } from "../api/hooks";
import { SectionRenderer } from "../components/Sections/SectionRenderer";

export const PreparationPage: React.FC<{ runId: string | null }> = ({ runId }) => {
  const { data: section, isLoading, error } = useSection(runId, "preparation");

  return (
    <Box>
      {runId && (
        <Stack direction="row" spacing={1.5} justifyContent="flex-end" sx={{ mb: 2 }}>
          <Button
            size="small"
            variant="outlined"
            startIcon={<DownloadIcon fontSize="small" />}
            href={`/api/v1/runs/${runId}/artifacts/data/wide_2023_2024.parquet`}
            target="_blank"
          >
            Download wide_2023_2024.parquet
          </Button>
          <Button
            size="small"
            variant="outlined"
            startIcon={<DownloadIcon fontSize="small" />}
            href={`/api/v1/runs/${runId}/artifacts/data/meridian_input.parquet`}
            target="_blank"
          >
            Download meridian_input.parquet
          </Button>
        </Stack>
      )}
      <SectionRenderer section={section} isLoading={isLoading} error={error as Error | null} />
    </Box>
  );
};
