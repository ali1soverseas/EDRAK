/** Typed calls to the product endpoints beyond the MVP screens. Same cookie session as httpApi. */
import type { ActionPlanDoc, ActionPlanRow, ChallengeResult } from "../types/brief3";

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";

export class HttpError extends Error {
  constructor(readonly status: number, readonly detail: unknown) {
    super(typeof detail === "string" ? detail : (detail as { message?: string })?.message ?? `HTTP ${status}`);
  }
}

export async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const isForm = body instanceof FormData;
  const res = await fetch(`${BASE}/api${path}`, {
    method,
    credentials: "include",
    headers: body && !isForm ? { "Content-Type": "application/json" } : undefined,
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* not JSON */
    }
    throw new HttpError(res.status, detail);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const apiUrl = (path: string) => `${BASE}/api${path}`;

/* ---------------------------------------------------------------- types */

export interface Link { platform: string; url: string; verified?: boolean; found_on_url?: string | null }
export interface Competitor {
  entity_id?: string; name: string; website?: string | null; website_domain?: string | null;
  discovered_by?: string; discovery_reason?: string | null; discovery_confidence?: number | null; links: Link[];
}
export interface Profile {
  name: string; aliases: string[]; industry: string | null; industries: string[]; industry_other: string | null; description: string;
  offerings: string[]; target_customers: string; target_customer_groups: string[]; goals: string[];
  markets: { entity_id?: string; name: string; relation: "serves" | "considers" }[];
  website: string; links: { id?: string; platform: string; url: string }[]; competitors: Competitor[];
  onboarding_step: number; completed: boolean; completed_at?: string | null; updated_at?: string | null;
}
export interface Onboarding { step: number; total_steps: number; completed: boolean; missing: string[]; profile: Profile }
export interface Candidate {
  name: string; website: string | null; reason: string; confidence: number; links: Link[]; evidence_urls: string[]; already_tracked: boolean;
}
export interface Doc {
  document_id: string; filename: string; status: string; kind: string; size: { unit: string; value: number } | null;
  mime_type: string | null; size_bytes: number | null; index_progress: number | null; error_message: string | null;
  added_by: string | null; added_at: string | null; used_in_runs: number; guardrail_status: "clean" | "flagged" | "quarantined";
  guardrail_notes: string[]; retrievability: number | null; is_synthetic: boolean;
}
export interface Preview {
  document: Doc; view: "pdf" | "table" | "text" | "unavailable"; text: string | null; truncated: boolean;
  sheets: { name: string; columns: string[]; rows: string[][] }[];
  chunks: { id: string; chunk_index: number; content: string; locator: string | null; flags: string[]; excluded: boolean }[];
  message: string | null;
}
export type Dict = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export interface StreamEvent { type: "step" | "answer" | "error"; data: Dict }

/* ---------------------------------------------------------------- endpoints */

export const edrak = {
  onboarding: () => req<Onboarding>("GET", "/onboarding"),
  saveOnboarding: (patch: Dict) => req<Onboarding>("PUT", "/onboarding", patch),
  completeOnboarding: () => req<Onboarding>("POST", "/onboarding/complete"),
  discover: (limit = 5) => req<Candidate[]>("POST", "/entities/discover", { limit }),

  documents: () => req<Doc[]>("GET", "/documents"),
  upload: (files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return req<{ documents: Doc[]; errors: { filename: string; code: string; message: string }[] }>("POST", "/documents", fd);
  },
  removeDocument: (id: string) => req("DELETE", `/documents/${id}`),
  retryDocument: (id: string) => req<Doc>("POST", `/documents/${id}/retry`),
  preview: (id: string) => req<Preview>("GET", `/documents/${id}/preview`),
  fileUrl: (id: string, download = false) => apiUrl(`/documents/${id}/file${download ? "?download=1" : ""}`),

  home: () => req<Dict>("GET", "/home"),
  reports: (q = "") => req<Dict[]>("GET", `/reports${q}`),
  compare: (a: string, b: string) => req<Dict>("GET", `/reports/compare?a=${a}&b=${b}`),
  decisions: () => req<Dict[]>("GET", "/decisions"),
  notifications: () => req<{ items: Dict[]; unread: number }>("GET", "/notifications"),
  readNotifications: (id?: string) => req("POST", `/notifications/read${id ? `?notification_id=${id}` : ""}`),
  search: (q: string) => req<Record<string, Dict[]>>("GET", `/search?q=${encodeURIComponent(q)}`),
  settings: () => req<Dict>("GET", "/settings"),
  saveSettings: (body: Dict) => req<Dict>("PUT", "/settings", body),

  entities: (kind?: string) => req<Dict[]>("GET", `/entities${kind ? `?kind=${kind}` : ""}`),
  createEntity: (body: Dict) => req<Dict>("POST", "/entities", body),
  track: (id: string, tracked: boolean) => req("POST", `/entities/${id}/track?tracked=${tracked}`),
  refreshLinks: (id: string) => req<Dict>("POST", `/entities/${id}/links/refresh`),

  monitors: () => req<Dict[]>("GET", "/monitors"),
  saveMonitor: (body: Dict, id?: string) => req<Dict>(id ? "PUT" : "POST", id ? `/monitors/${id}` : "/monitors", body),
  deleteMonitor: (id: string) => req("DELETE", `/monitors/${id}`),
  runMonitor: (id: string) => req<{ analysis_id: string }>("POST", `/monitors/${id}/run-now`),
  alerts: () => req<{ alerts: Dict[]; digests: Dict[] }>("GET", "/alerts"),
  actOnAlert: (id: string, action: string) => req<Dict>("POST", `/alerts/${id}`, { action }),
  readAllAlerts: () => req("POST", "/alerts/read-all"),

  review: (id: string, decision: string, tasks: Dict[] = [], note?: string) =>
    req("POST", `/analyses/${id}/review`, { decision, tasks, note }),
  pause: (id: string) => req("POST", `/analyses/${id}/pause`),
  resume: (id: string) => req("POST", `/analyses/${id}/resume`),
  notifyInstead: (id: string) => req("POST", `/analyses/${id}/notify-instead`),

  quality: (briefId: string) => req<Dict>("GET", `/briefs/${briefId}/quality`),
  exportUrl: (briefId: string, format: string) => apiUrl(`/briefs/${briefId}/export?format=${format}`),
  reviewBrief: (briefId: string, action: string, note?: string) => req<Dict>("POST", `/briefs/${briefId}/review`, { action, note }),
  recordDecision: (briefId: string, body: Dict) => req<Dict>("POST", `/briefs/${briefId}/decision`, body),
  undoDecision: (briefId: string) => req("DELETE", `/briefs/${briefId}/decision`),
  evidence: (briefId: string) => req<Dict[]>("GET", `/briefs/${briefId}/evidence`),
  annotate: (briefId: string, body: Dict) => req("POST", `/briefs/${briefId}/annotations`, body),

  actionPlans: (briefId: string) => req<ActionPlanRow[]>("GET", `/briefs/${briefId}/action-plans`),
  createActionPlan: (briefId: string, optionKey: string, note?: string) =>
    req<{ plan_id: string; status: string }>("POST", `/briefs/${briefId}/action-plan`, { option_key: optionKey, note }),
  actionPlan: (planId: string) => req<ActionPlanRow>("GET", `/action-plans/${planId}`),
  saveActionPlan: (planId: string, document: ActionPlanDoc, note?: string) => req<ActionPlanRow>("PUT", `/action-plans/${planId}`, { document, note }),
  regenerateActionPlan: (planId: string, optionKey: string, note?: string) =>
    req<{ plan_id: string; status: string }>("POST", `/action-plans/${planId}/regenerate`, { option_key: optionKey, note }),
  approveActionPlan: (planId: string, body: { title?: string; rationale?: string } = {}) =>
    req<Dict>("POST", `/action-plans/${planId}/approve`, body),

  cardVersions: (briefId: string, cardKey: string) => req<Dict[]>("GET", `/briefs/${briefId}/cards/${encodeURIComponent(cardKey)}/versions`),
  challengeCard: (briefId: string, cardKey: string, body: { source: "user" | "web"; text: string; url?: string; title?: string; entity?: string }) =>
    req<ChallengeResult>("POST", `/briefs/${briefId}/cards/${encodeURIComponent(cardKey)}/challenge`, body),

  /** Streams an answer about a brief: `step` events while tools run, then one `answer`. */
  streamAsk: async (thread: string, content: string, onEvent: (e: StreamEvent) => void): Promise<void> => {
    const res = await fetch(apiUrl(`/chat/threads/${thread}/stream`), {
      method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content }),
    });
    if (!res.ok || !res.body) {
      let detail: unknown = res.statusText;
      try { detail = (await res.json()).detail; } catch { /* not JSON */ }
      throw new HttpError(res.status, detail);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut: number;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const data = block.split("\n").find((l) => l.startsWith("data: "));
        if (!data) continue;
        try {
          const parsed = JSON.parse(data.slice(6)) as { type: StreamEvent["type"]; data: Dict };
          onEvent({ type: parsed.type, data: parsed.data });
        } catch { /* skip a malformed block */ }
      }
    }
  },

  newThread: (scope: Dict) => req<{ thread_id: string }>("POST", "/chat/threads", scope),
  messages: (thread: string) => req<Dict[]>("GET", `/chat/threads/${thread}/messages`),
  ask: (thread: string, content: string) => req<Dict>("POST", `/chat/threads/${thread}/messages`, { content }),
};
