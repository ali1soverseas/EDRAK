/**
 * SSE (Server-Sent Events) listener for live run progression.
 */
import type { RunEvent, RunStage, RunState, RunTaskView } from "../types/app";

export interface StreamCallbacks {
  onStageChange?: (stage: RunStage) => void;
  onTaskUpdate?: (task: RunTaskView) => void;
  onEvent?: (event: RunEvent) => void;
  onVerification?: (status: "waiting" | "running" | "done") => void;
  onComplete?: (data: { state: RunState; brief_id: string | null }) => void;
  onError?: (err: { message: string }) => void;
}

export function subscribeToRun(
  analysisId: string,
  callbacks: StreamCallbacks,
  baseUrl: string = "",
): () => void {
  const url = `${baseUrl}/api/analyses/${encodeURIComponent(analysisId)}/run/stream`;
  const es = new EventSource(url, { withCredentials: true });

  const handlePayload = (type: string, data: any) => {
    switch (type) {
      case "stage_change":
        callbacks.onStageChange?.(data.stage);
        break;
      case "task_update":
        callbacks.onTaskUpdate?.(data);
        break;
      case "event":
        callbacks.onEvent?.(data);
        break;
      case "verification":
        callbacks.onVerification?.(data.status);
        break;
      case "complete":
        callbacks.onComplete?.(data);
        es.close();
        break;
      case "error":
        callbacks.onError?.(data);
        break;
    }
  };

  es.onmessage = (e) => {
    try {
      const parsed = JSON.parse(e.data);
      if (parsed && parsed.type) {
        handlePayload(parsed.type, parsed.data);
      }
    } catch {
      // ignore parse errors or ping comments
    }
  };

  const eventTypes = [
    "stage_change",
    "task_update",
    "event",
    "verification",
    "complete",
    "error",
  ];

  for (const t of eventTypes) {
    es.addEventListener(t, (e: any) => {
      try {
        const parsed = JSON.parse(e.data);
        handlePayload(t, parsed.data ?? parsed);
      } catch {
        // ignore
      }
    });
  }

  es.onerror = () => {
    // If the stream ends or server disconnects
    callbacks.onError?.({ message: "SSE connection lost" });
  };

  return () => {
    es.close();
  };
}
