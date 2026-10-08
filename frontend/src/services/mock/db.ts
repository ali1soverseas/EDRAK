/**
 * The mock database: one object, persisted to localStorage so a reload keeps your
 * work. It stands in for the tables in mvp_screens_to_tables.md.
 */
import type {
  AnalysisForm,
  CompanyDocument,
  CompanySetup,
  Workspace,
} from "../../types/app";
import type { BusinessRequest, ResearchPlan } from "../../types/contracts";
import type { RunRecord } from "./simulate";

const STORAGE_KEY = "edrak.mock.db.v1";

export interface StoredUser {
  user_id: string;
  email: string;
  /** SHA-256 of "email:password". Mock only, there is no real authentication yet. */
  password_hash: string;
  name: string;
  title: string | null;
  workspace_id: string;
}

export type StoredStatus = "draft" | "awaiting_approval" | "running" | "cancelled";

export interface AnalysisRecord {
  analysis_id: string;
  title: string;
  form: AnalysisForm;
  request: BusinessRequest;
  plan: ResearchPlan | null;
  status: StoredStatus;
  rejection_reason: string | null;
  updated_at: string;
  run: RunRecord | null;
  brief_no: number | null;
}

export interface StoredDocument extends CompanyDocument {
  /** Epoch milliseconds when indexing finishes. */
  ready_at: number | null;
  will_fail: boolean;
}

export interface StoredCompany extends Omit<CompanySetup, "documents"> {
  documents: StoredDocument[];
}

export interface MockDb {
  version: 1;
  users: StoredUser[];
  workspaces: Workspace[];
  companies: Record<string, StoredCompany>;
  analyses: Record<string, AnalysisRecord[]>;
  next_brief_no: Record<string, number>;
  session_user_id: string | null;
}

let cache: MockDb | null = null;

export function loadDb(create: () => MockDb): MockDb {
  if (cache) return cache;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as MockDb;
      if (parsed.version === 1) {
        cache = parsed;
        return cache;
      }
    }
  } catch {
    // Missing or unreadable storage: start from the seed.
  }
  cache = create();
  saveDb();
  return cache;
}

export function saveDb(): void {
  if (!cache) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(cache));
  } catch {
    // Storage can be full or blocked. The mock keeps working in memory.
  }
}

export function resetDb(): void {
  cache = null;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to clear.
  }
}
