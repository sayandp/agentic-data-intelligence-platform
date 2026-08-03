import { useCallback, useRef, useState } from "react";

export interface ToastState {
  message: string;
  kind: "success" | "error";
}

const AUTO_DISMISS_MS = 5000;

// Page-scoped, not a global app-wide notification system - each page that
// takes actions (Approvals' four decisions, Sources' ingest/upload/register)
// owns its own toast, since "what just happened" is only meaningful in the
// context of the list it happened on.
export function useToast() {
  const [toast, setToast] = useState<ToastState | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = useCallback((message: string, kind: ToastState["kind"] = "success") => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setToast({ message, kind });
    timerRef.current = setTimeout(() => setToast(null), AUTO_DISMISS_MS);
  }, []);

  const dismissToast = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setToast(null);
  }, []);

  return { toast, showToast, dismissToast };
}
