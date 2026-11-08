import React, { useEffect, useRef } from "react";
import { Button, Paper, Skeleton, Typography } from "@mui/material";
import { Link as RouterLink, useLocation } from "react-router-dom";
import { PALETTE } from "../../theme";
import { PageHeader } from "../Layout/PageHeader";

/**
 * The title of a run page. After an action that moves the user here (upload, start), focus goes to the heading so
 * screen readers announce the new page.
 */
export const RunPageHeader: React.FC<{ title: string; children?: React.ReactNode }> = ({ title, children }) => {
  const heading = useRef<HTMLHeadingElement>(null);
  const location = useLocation();
  useEffect(() => {
    if ((location.state as { focusHeading?: boolean } | null)?.focusHeading) heading.current?.focus();
  }, [location.key, location.state]);
  return <PageHeader title={title} description={children} headingRef={heading} />;
};

export const NoRunSelected: React.FC<{ loading: boolean }> = ({ loading }) =>
  loading ? (
    <Paper sx={{ p: 3 }} aria-busy="true">
      <Skeleton width="30%" />
      <Skeleton width="55%" height={36} />
      <Skeleton width="70%" />
    </Paper>
  ) : (
    <Paper sx={{ p: 4, textAlign: "center", borderStyle: "dashed", backgroundColor: PALETTE.bgSubtle }}>
      <Typography variant="h3" component="h2">
        No run selected
      </Typography>
      <Typography variant="body2" sx={{ mt: 0.5, mb: 2 }}>
        Pick a run in the header, or upload a workbook to create one.
      </Typography>
      <Button variant="contained" component={RouterLink} to="/runs">
        Go to runs
      </Button>
    </Paper>
  );
