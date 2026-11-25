import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  message: string;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: "" };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, message: error.message || "Unexpected rendering error" };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("WEBNUT UI error:", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-6 space-y-3">
          <div className="flex items-center gap-2 text-rose-300 font-semibold">
            <AlertTriangle className="h-5 w-5" />
            <span>Something went wrong rendering this view</span>
          </div>
          <p className="text-xs font-mono text-rose-200">{this.state.message}</p>
          <button
            onClick={() => this.setState({ hasError: false, message: "" })}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-xs text-white"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Retry View
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
