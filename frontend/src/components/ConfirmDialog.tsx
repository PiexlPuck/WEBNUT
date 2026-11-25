import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";
import { Modal } from "./Modal";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "danger" | "warning" | "primary";
  busy?: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  variant = "danger",
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  useEffect(() => {
    if (!open || busy) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, busy, onCancel]);

  const iconClasses =
    variant === "danger"
      ? "bg-rose-500/15 text-rose-400 border-rose-500/30"
      : variant === "warning"
      ? "bg-amber-500/15 text-amber-400 border-amber-500/30"
      : "bg-indigo-500/15 text-indigo-400 border-indigo-500/30";

  const confirmBtnClasses =
    variant === "danger"
      ? "bg-rose-600 hover:bg-rose-500"
      : variant === "warning"
      ? "bg-amber-600 hover:bg-amber-500"
      : "bg-indigo-600 hover:bg-indigo-500";

  return (
    <Modal
      open={open}
      title={title}
      onClose={() => {
        if (!busy) onCancel();
      }}
      maxWidth="max-w-md"
    >
      <div className="space-y-4">
        <div className="flex items-start gap-3">
          <div
            className={`h-9 w-9 rounded-xl border flex items-center justify-center shrink-0 ${iconClasses}`}
          >
            <AlertTriangle className="h-4 w-4" />
          </div>
          <p className="text-sm text-slate-300 leading-relaxed pt-1">{message}</p>
        </div>

        <div className="flex justify-end gap-2 pt-3 border-t border-slate-800">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="px-4 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 text-xs font-medium text-slate-300 disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={() => void onConfirm()}
            disabled={busy}
            className={`px-4 py-2 rounded-lg text-xs font-medium text-white disabled:opacity-50 ${confirmBtnClasses}`}
          >
            {busy ? "Working..." : confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}
