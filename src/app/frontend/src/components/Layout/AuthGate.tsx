import React, { useEffect, useState } from "react";
import { Alert, Box, Button, CircularProgress, Paper, Stack, TextField, Typography } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { api, onAuthRequired } from "../../api/client";
import { useAuthSession } from "../../api/hooks";
import { PALETTE } from "../../theme";
import { BrandMark } from "./BrandMark";

const errorText = (err: unknown) => (err instanceof Error && err.message ? err.message : "Sign-in failed.");

/** The sign-in screen: the access code is exchanged for an HttpOnly session cookie and never stored in the page. */
const SignIn: React.FC = () => {
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.login(code.trim());
      setCode("");
      queryClient.clear();
      await queryClient.invalidateQueries({ queryKey: ["auth-session"] });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box component="main" sx={{ minHeight: "100vh", display: "grid", placeItems: "center", px: 2, backgroundColor: PALETTE.bgCanvas }}>
      <Paper component="form" onSubmit={submit} aria-labelledby="sign-in-title" sx={{ p: { xs: 3, sm: 4 }, width: "100%", maxWidth: 420 }}>
        <Stack spacing={2.5}>
          <BrandMark />
          <Box>
            <Typography id="sign-in-title" variant="h2" component="h1" sx={{ mb: 0.5 }}>
              Sign in
            </Typography>
            <Typography variant="body2" sx={{ color: PALETTE.inkSecondary }}>
              Workbooks, models and results in this studio are private. Enter the studio access code to continue.
            </Typography>
          </Box>
          {error && (
            <Alert severity="error" onClose={() => setError(null)}>
              {error}
            </Alert>
          )}
          <TextField
            fullWidth
            size="small"
            type="password"
            label="Access code"
            autoComplete="current-password"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            autoFocus
            required
          />
          <Button type="submit" variant="contained" disabled={busy || !code.trim()} startIcon={busy ? <CircularProgress size={14} color="inherit" /> : undefined}>
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </Stack>
      </Paper>
    </Box>
  );
};

/** Shows the app only to a signed-in browser when the server requires an access code; any 401 re-checks the session. */
export const AuthGate: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const queryClient = useQueryClient();
  const { data: session, isLoading, error } = useAuthSession();

  useEffect(() => onAuthRequired(() => void queryClient.invalidateQueries({ queryKey: ["auth-session"] })), [queryClient]);

  if (isLoading) {
    return (
      <Box sx={{ minHeight: "100vh", display: "grid", placeItems: "center" }}>
        <CircularProgress aria-label="Loading" />
      </Box>
    );
  }
  if (error && !session) {
    return (
      <Box sx={{ minHeight: "100vh", display: "grid", placeItems: "center", px: 2 }}>
        <Alert severity="error">The studio's server cannot be reached. Reload the page in a moment.</Alert>
      </Box>
    );
  }
  if (session?.auth_required && !session.authenticated) return <SignIn />;
  return <>{children}</>;
};
