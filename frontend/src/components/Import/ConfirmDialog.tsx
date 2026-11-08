import React, { useRef } from "react";
import { Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle } from "@mui/material";

export interface ConfirmRequest {
  title: string;
  body: string;
  /** The button that closes the dialog without acting; it gets the focus */
  safeLabel: string;
  confirmLabel: string;
  tone?: "primary" | "warning" | "error";
}

/**
 * A confirmation for a destructive or expensive action. The safe choice is focused once the dialog has opened (MUI's
 * focus trap lands on the container), and the last request stays visible while the dialog animates closed.
 */
export const ConfirmDialog: React.FC<{ request: ConfirmRequest | null; onCancel: () => void; onConfirm: () => void }> = ({
  request,
  onCancel,
  onConfirm,
}) => {
  const safeChoice = useRef<HTMLButtonElement>(null);
  const last = useRef<ConfirmRequest | null>(null);
  if (request) last.current = request;
  const shown = request ?? last.current;

  return (
    <Dialog
      open={request !== null}
      onClose={onCancel}
      maxWidth="xs"
      fullWidth
      aria-labelledby="confirm-title"
      TransitionProps={{ onEntered: () => safeChoice.current?.focus() }}
    >
      {shown && (
        <>
          <DialogTitle id="confirm-title">{shown.title}</DialogTitle>
          <DialogContent>
            <DialogContentText>{shown.body}</DialogContentText>
          </DialogContent>
          <DialogActions>
            <Button ref={safeChoice} onClick={onCancel}>
              {shown.safeLabel}
            </Button>
            <Button color={shown.tone ?? "primary"} variant="contained" onClick={onConfirm}>
              {shown.confirmLabel}
            </Button>
          </DialogActions>
        </>
      )}
    </Dialog>
  );
};
