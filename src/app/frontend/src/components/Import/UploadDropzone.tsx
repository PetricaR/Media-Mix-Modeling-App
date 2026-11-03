import React, { useId, useRef, useState } from "react";
import { Alert, Box, CircularProgress, Paper, Typography } from "@mui/material";
import CloudUploadOutlinedIcon from "@mui/icons-material/CloudUploadOutlined";
import { PALETTE } from "../../theme";

const FORMAT_HINT = "Excel .xlsx in long format: date, channel, sales and costs (impressions and clicks optional). Up to 10 MB.";

/**
 * Drop zone for a new workbook. The zone is a <label> around a visually hidden file input, so a click anywhere opens
 * the picker and keyboard users reach one native control; the "Choose a workbook" pill is decoration, not a button.
 */
export const UploadDropzone: React.FC<{
  variant: "hero" | "compact";
  uploadingName: string | null;
  error: string | null;
  onClearError: () => void;
  onFile: (file: File) => void;
}> = ({ variant, uploadingName, error, onClearError, onFile }) => {
  const inputId = useId();
  const hintId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const uploading = uploadingName !== null;
  const hero = variant === "hero";

  const pick = (file?: File | null) => {
    if (file && !uploading) onFile(file);
    if (input.current) input.current.value = "";
  };

  const zone = (
    <Box
      component="label"
      htmlFor={inputId}
      aria-busy={uploading || undefined}
      onDragEnter={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={(e) => {
        // Moving onto a child fires dragleave on the zone: only reset when the pointer really leaves it
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragOver(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        pick(e.dataTransfer.files?.[0]);
      }}
      sx={{
        display: "flex",
        flexDirection: hero ? "column" : "row",
        alignItems: "center",
        justifyContent: hero ? "center" : "flex-start",
        textAlign: hero ? "center" : "left",
        gap: hero ? 1.5 : 1.75,
        py: hero ? { xs: 5, sm: 7 } : 2.25,
        px: hero ? 3 : 2,
        cursor: uploading ? "progress" : "pointer",
        borderRadius: hero ? 3 : 2,
        border: `2px dashed ${dragOver ? PALETTE.brandGold : PALETTE.borderStrong}`,
        backgroundColor: dragOver ? PALETTE.bgGoldTint : PALETTE.bgSubtle,
        transition: "background-color 0.15s, border-color 0.15s",
        "&:hover": { backgroundColor: uploading ? undefined : PALETTE.bgGoldTint },
        "&:has(input:focus-visible)": { outline: `3px solid ${PALETTE.brandPrimary}`, outlineOffset: 2 },
        "@media (prefers-reduced-motion: reduce)": { transition: "none" },
      }}
    >
      <Box
        component="span"
        aria-hidden="true"
        sx={{
          flexShrink: 0,
          width: hero ? 64 : 44,
          height: hero ? 64 : 44,
          borderRadius: "50%",
          display: "grid",
          placeItems: "center",
          backgroundColor: "#FFFFFF",
          border: `1px solid ${PALETTE.borderSubtle}`,
          color: PALETTE.brandGold,
        }}
      >
        {uploading ? <CircularProgress size={hero ? 30 : 22} color="secondary" /> : <CloudUploadOutlinedIcon sx={{ fontSize: hero ? 32 : 24 }} />}
      </Box>

      <Box component="span" sx={{ display: "block", minWidth: 0 }}>
        {uploading ? (
          <Typography component="span" sx={{ display: "block", fontWeight: 700, overflowWrap: "anywhere" }}>
            Uploading and profiling {uploadingName}…
          </Typography>
        ) : hero ? (
          <>
            <Typography variant="h2" component="span" sx={{ display: "block", mb: 0.75 }}>
              {dragOver ? "Release to upload" : "Upload the case-study workbook"}
            </Typography>
            <Typography variant="body1" component="span" sx={{ display: "block", color: PALETTE.inkSecondary }}>
              Drag <Box component="span" sx={{ fontWeight: 650, color: PALETTE.inkPrimary }}>DataScienceMarketingUseCase.xlsx</Box> here, or
            </Typography>
          </>
        ) : (
          <Typography component="span" sx={{ display: "block", fontWeight: 700, lineHeight: 1.35 }}>
            {dragOver ? (
              "Release to upload"
            ) : (
              <>
                Drop a workbook or{" "}
                <Box component="span" sx={{ color: PALETTE.brandPrimary, textDecoration: "underline", textUnderlineOffset: 3 }}>
                  browse
                </Box>
              </>
            )}
          </Typography>
        )}
      </Box>

      {hero && !uploading && (
        <Box
          component="span"
          aria-hidden="true"
          sx={{
            mt: 1,
            display: "inline-flex",
            alignItems: "center",
            minHeight: 44,
            px: 3,
            borderRadius: 999,
            fontWeight: 650,
            color: "#FFFFFF",
            backgroundColor: PALETTE.brandGold,
            "label:hover &": { backgroundColor: PALETTE.brandGoldDark },
          }}
        >
          Choose a workbook
        </Box>
      )}

      <input
        id={inputId}
        ref={input}
        type="file"
        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="visually-hidden"
        aria-label="Upload a workbook (.xlsx)"
        aria-describedby={hintId}
        disabled={uploading}
        onChange={(e) => pick(e.target.files?.[0])}
      />
    </Box>
  );

  return (
    <Paper component="section" aria-labelledby={hero ? undefined : `${inputId}-title`} aria-label={hero ? "Upload a workbook" : undefined} sx={{ p: hero ? { xs: 2, sm: 3 } : 2.5 }}>
      {!hero && (
        <Box sx={{ mb: 1.75 }}>
          <Typography id={`${inputId}-title`} variant="h3" component="h2">
            New run
          </Typography>
          <Typography variant="body2">Upload a workbook to create a draft you can review before it runs.</Typography>
        </Box>
      )}
      {zone}
      <Typography id={hintId} variant="caption" component="p" sx={{ color: PALETTE.inkSecondary, mt: 1.25, textAlign: hero ? "center" : "left" }}>
        {FORMAT_HINT}
      </Typography>
      {error && (
        <Alert severity="error" onClose={onClearError} sx={{ mt: 1.5 }}>
          {error}
        </Alert>
      )}
    </Paper>
  );
};
