/**
 * In-browser implementation of EdrakApi. State lives in localStorage (see ./db) and
 * runs are simulated from the clock (see ./simulate). It enforces the same rules the
 * real API must: nothing runs before the plan is approved, sign-in needs a known
 * account, and so on.
 */
import { ApiError, type EdrakApi } from "../api";
import type {
  AnalysisDetail,
  AnalysisStatus,
  AnalysisSummary,
  CompanyDocument,
  CompanyFields,
  CompanySetup,
  Session,
} from "../../types/app";
import { deriveTitle, formToRequest } from "../../lib/form";
import { newId } from "../../lib/ids";
import { briefFor } from "./results";
import { PACKS } from "./packs";
import { planFor } from "./planner";
import { loadDb, resetDb, saveDb, type AnalysisRecord, type MockDb, type StoredCompany, type StoredUser } from "./db";
import { createSeedDb, emptyCompany, passwordHash } from "./seed";
import { simulateRun, type RunRecord } from "./simulate";

const LATENCY_MS = import.meta.env.MODE === "test" ? 0 : 160;
const delay = () => new Promise<void>((resolve) => setTimeout(resolve, LATENCY_MS));

const ACCEPTED_EXTENSIONS = new Set(["pdf", "doc", "docx", "txt", "md", "csv"]);
const INDEXING_MS = 3500;

export const briefIdFor = (analysisId: string) => `brief-${analysisId}`;

const getDb = (): MockDb => loadDb(() => createSeedDb());

function requireUser(db: MockDb): StoredUser {
  const user = db.users.find((candidate) => candidate.user_id === db.session_user_id);
  if (!user) throw new ApiError("not_signed_in");
  return user;
}

function toSession(db: MockDb, user: StoredUser): Session {
  const workspace = db.workspaces.find((candidate) => candidate.workspace_id === user.workspace_id);
  if (!workspace) throw new ApiError("not_found", "workspace missing");
  return {
    user: { user_id: user.user_id, email: user.email, name: user.name, title: user.title },
    workspace,
  };
}

function nameFromEmail(email: string): string {
  const local = email.split("@")[0] ?? "";
  const words = local.split(/[._-]+/).filter(Boolean);
  if (words.length === 0) return "New user";
  return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
}

function records(db: MockDb, user: StoredUser): AnalysisRecord[] {
  return (db.analyses[user.workspace_id] ??= []);
}

function findRecord(db: MockDb, user: StoredUser, analysisId: string): AnalysisRecord {
  const record = records(db, user).find((candidate) => candidate.analysis_id === analysisId);
  if (!record) throw new ApiError("not_found", `analysis ${analysisId}`);
  return record;
}

/** What a record looks like at `now`: its status and the facts the list shows. */
function derive(record: AnalysisRecord, now: number) {
  const base = {
    status: record.status as AnalysisStatus,
    updated_at: record.updated_at,
    tasks_total: record.plan?.tasks.length ?? 0,
    tasks_done: 0,
    tasks_failed: 0,
    brief_id: null as string | null,
  };
  if (!record.run || !record.plan) return base;

  const view = simulateRun({
    analysisId: record.analysis_id,
    title: record.title,
    useCase: record.request.business_context.use_case,
    plan: record.plan,
    pack: PACKS[record.request.business_context.use_case],
    run: record.run,
    now,
    briefId: briefIdFor(record.analysis_id),
  });
  const status: AnalysisStatus = view.state === "running" ? "running" : view.state;
  return {
    status,
    updated_at: view.finished_at ?? record.run.approved_at,
    tasks_total: view.tasks.length,
    tasks_done: view.tasks.filter((task) => task.state === "done").length,
    tasks_failed: view.tasks.filter((task) => task.state === "failed").length,
    brief_id: view.brief_id,
  };
}

function toSummary(record: AnalysisRecord, now: number): AnalysisSummary {
  const state = derive(record, now);
  return {
    analysis_id: record.analysis_id,
    title: record.title,
    use_case: record.request.business_context.use_case,
    status: state.status,
    updated_at: state.updated_at,
    repeat_weekly: record.form.repeat_weekly,
    tasks_total: state.tasks_total,
    tasks_done: state.tasks_done,
    tasks_failed: state.tasks_failed,
    brief_id: state.brief_id,
    brief_no: state.brief_id ? record.brief_no : null,
    verified: state.status === "completed",
  };
}

/** Resolves any document whose indexing time has passed. */
function settleDocuments(company: StoredCompany, now: number): boolean {
  let changed = false;
  for (const doc of company.documents) {
    if (doc.status === "indexing" && doc.ready_at !== null && doc.ready_at <= now) {
      doc.status = doc.will_fail ? "failed" : "indexed";
      doc.ready_at = null;
      changed = true;
    }
  }
  return changed;
}

const publicDocument = ({ ready_at: _readyAt, will_fail: _willFail, ...doc }: StoredCompany["documents"][number]): CompanyDocument => doc;

const publicCompany = (company: StoredCompany): CompanySetup => ({
  ...company,
  documents: company.documents.map(publicDocument),
});

export const mockApi: EdrakApi = {
  async getSession() {
    await delay();
    const db = getDb();
    const user = db.users.find((candidate) => candidate.user_id === db.session_user_id);
    return user ? toSession(db, user) : null;
  },

  async signIn(email, password) {
    await delay();
    const db = getDb();
    const normalized = email.trim().toLowerCase();
    const user = db.users.find((candidate) => candidate.email === normalized);
    if (!user || user.password_hash !== passwordHash(normalized, password)) {
      throw new ApiError("invalid_credentials");
    }
    db.session_user_id = user.user_id;
    saveDb();
    return toSession(db, user);
  },

  async createAccount(email, password) {
    await delay();
    const db = getDb();
    const normalized = email.trim().toLowerCase();
    if (db.users.some((candidate) => candidate.email === normalized)) throw new ApiError("email_taken");

    const name = nameFromEmail(normalized);
    const workspaceId = `ws-${newId()}`;
    const user: StoredUser = {
      user_id: `user-${newId()}`,
      email: normalized,
      password_hash: passwordHash(normalized, password),
      name,
      title: null,
      workspace_id: workspaceId,
    };
    db.users.push(user);
    db.workspaces.push({ workspace_id: workspaceId, name: `${name}'s workspace` });
    db.companies[workspaceId] = emptyCompany();
    db.analyses[workspaceId] = [];
    db.next_brief_no[workspaceId] = 1;
    db.session_user_id = user.user_id;
    saveDb();
    return toSession(db, user);
  },

  async signOut() {
    await delay();
    const db = getDb();
    db.session_user_id = null;
    saveDb();
  },

  async getCompany() {
    await delay();
    const db = getDb();
    const user = requireUser(db);
    const company = (db.companies[user.workspace_id] ??= emptyCompany());
    if (settleDocuments(company, Date.now())) saveDb();
    return publicCompany(company);
  },

  async saveCompany(fields: CompanyFields) {
    await delay();
    const db = getDb();
    const user = requireUser(db);
    const company = (db.companies[user.workspace_id] ??= emptyCompany());
    const seenUrls = new Set<string>();
    const socials = fields.socials.filter((link) => {
      const key = link.url.trim().toLowerCase();
      if (!key || seenUrls.has(key)) return false;
      seenUrls.add(key);
      return true;
    });
    Object.assign(company, fields, { socials, completed: true });
    const workspace = db.workspaces.find((candidate) => candidate.workspace_id === user.workspace_id);
    if (workspace && fields.name.trim()) workspace.name = fields.name.trim();
    saveDb();
    return publicCompany(company);
  },

  async uploadDocuments(files) {
    await delay();
    const db = getDb();
    const user = requireUser(db);
    const company = (db.companies[user.workspace_id] ??= emptyCompany());
    const added = files.map((file) => {
      const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
      const stored: StoredCompany["documents"][number] = {
        document_id: `doc-${newId()}`,
        filename: file.name,
        size: { unit: "kb", value: Math.max(1, Math.round(file.size / 1024)) },
        status: "indexing",
        ready_at: Date.now() + INDEXING_MS,
        will_fail: !ACCEPTED_EXTENSIONS.has(extension),
      };
      company.documents.push(stored);
      return publicDocument(stored);
    });
    saveDb();
    return added;
  },

  async listDocuments() {
    await delay();
    const db = getDb();
    const user = requireUser(db);
    const company = (db.companies[user.workspace_id] ??= emptyCompany());
    if (settleDocuments(company, Date.now())) saveDb();
    return company.documents.map(publicDocument);
  },

  async removeDocument(documentId) {
    await delay();
    const db = getDb();
    const user = requireUser(db);
    const company = (db.companies[user.workspace_id] ??= emptyCompany());
    company.documents = company.documents.filter((doc) => doc.document_id !== documentId);
    saveDb();
  },

  async listAnalyses() {
    await delay();
    const db = getDb();
    const user = requireUser(db);
    const now = Date.now();
    return records(db, user)
      .map((record) => toSummary(record, now))
      .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
  },

  async getAnalysis(analysisId) {
    await delay();
    const db = getDb();
    const user = requireUser(db);
    const record = findRecord(db, user, analysisId);
    const state = derive(record, Date.now());
    const detail: AnalysisDetail = {
      analysis_id: record.analysis_id,
      title: record.title,
      status: state.status,
      request: record.request,
      plan: record.plan,
      form: record.form,
      rejection_reason: record.rejection_reason,
      allowed_sources: (record.request.extras.allowed_sources as AnalysisDetail["allowed_sources"]) ?? [],
    };
    return detail;
  },

  async draftPlan(form, analysisId) {
    await delay();
    const db = getDb();
    const user = requireUser(db);
    const company = (db.companies[user.workspace_id] ??= emptyCompany());
    const list = records(db, user);
    const existing = analysisId ? list.find((candidate) => candidate.analysis_id === analysisId) : undefined;
    if (existing && existing.status !== "draft") throw new ApiError("wrong_state", "only a draft can be re-planned");

    const id = existing?.analysis_id ?? newId();
    const request = formToRequest(form, company, id);
    const plan = planFor(request, form);
    const record: AnalysisRecord = {
      analysis_id: id,
      title: deriveTitle(form),
      form,
      request,
      plan,
      status: "awaiting_approval",
      rejection_reason: null,
      updated_at: plan.created_at,
      run: null,
      brief_no: null,
    };
    if (existing) Object.assign(existing, record);
    else list.push(record);
    saveDb();
    return {
      analysis_id: id,
      title: record.title,
      status: record.status,
      request,
      plan,
      form,
      rejection_reason: null,
      allowed_sources: (request.extras.allowed_sources as AnalysisDetail["allowed_sources"]) ?? [],
    };
  },

  async approvePlan(analysisId) {
    await delay();
    const db = getDb();
    const user = requireUser(db);
    const record = findRecord(db, user, analysisId);
    if (record.status !== "awaiting_approval" || !record.plan) {
      throw new ApiError("wrong_state", "there is no plan waiting for approval");
    }
    const run: RunRecord = {
      approved_at: new Date().toISOString(),
      approved_by: user.name,
      speed: 1,
      // Demo behaviour: market-entry runs lose the Market worker so that state can be seen.
      scenario: record.request.business_context.use_case === "market_entry_expansion" ? "market_fails" : "normal",
      cancelled_at: null,
    };
    record.run = run;
    record.status = "running";
    record.updated_at = run.approved_at;
    record.brief_no = db.next_brief_no[user.workspace_id] ?? 1;
    db.next_brief_no[user.workspace_id] = record.brief_no + 1;
    saveDb();
  },

  async rejectPlan(analysisId, reason) {
    await delay();
    const db = getDb();
    const user = requireUser(db);
    const record = findRecord(db, user, analysisId);
    if (record.status !== "awaiting_approval") throw new ApiError("wrong_state", "there is no plan to reject");
    record.status = "draft";
    record.plan = null;
    record.rejection_reason = reason.trim() || null;
    record.updated_at = new Date().toISOString();
    saveDb();
  },

  async rerun(analysisId) {
    const db = getDb();
    const user = requireUser(db);
    const record = findRecord(db, user, analysisId);
    const detail = await mockApi.draftPlan({ ...record.form }, undefined);
    return { analysis_id: detail.analysis_id };
  },

  async getRun(analysisId) {
    await delay();
    const db = getDb();
    const user = requireUser(db);
    const record = findRecord(db, user, analysisId);
    if (!record.run || !record.plan) throw new ApiError("wrong_state", "this analysis has not started");
    return simulateRun({
      analysisId: record.analysis_id,
      title: record.title,
      useCase: record.request.business_context.use_case,
      plan: record.plan,
      pack: PACKS[record.request.business_context.use_case],
      run: record.run,
      now: Date.now(),
      briefId: briefIdFor(record.analysis_id),
    });
  },

  async cancelRun(analysisId) {
    await delay();
    const db = getDb();
    const user = requireUser(db);
    const record = findRecord(db, user, analysisId);
    if (!record.run) throw new ApiError("wrong_state", "this analysis has not started");
    if (record.run.cancelled_at === null) {
      record.run.cancelled_at = new Date().toISOString();
      record.status = "cancelled";
      record.updated_at = record.run.cancelled_at;
      saveDb();
    }
  },

  async getBrief(briefId) {
    await delay();
    const db = getDb();
    const user = requireUser(db);
    const record = records(db, user).find((candidate) => briefIdFor(candidate.analysis_id) === briefId);
    if (!record || !record.run || !record.plan || record.brief_no === null) throw new ApiError("not_found", `brief ${briefId}`);
    const state = derive(record, Date.now());
    if (state.status !== "completed" && state.status !== "partial") throw new ApiError("not_found", `brief ${briefId}`);
    return briefFor({
      briefId,
      briefNo: record.brief_no,
      analysisId: record.analysis_id,
      title: record.title,
      requestedBy: record.run.approved_by,
      request: record.request,
      plan: record.plan,
      pack: PACKS[record.request.business_context.use_case],
      run: record.run,
    });
  },
};

/** Dev only: `edrakMock.reset()` in the console restores the seed data. */
if (import.meta.env.DEV && typeof window !== "undefined") {
  (window as unknown as { edrakMock: { reset: () => void } }).edrakMock = {
    reset: () => {
      resetDb();
      window.location.reload();
    },
  };
}
