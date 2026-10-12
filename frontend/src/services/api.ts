/**
 * The MVP screens' view of the backend (sign in, company, analyses, plan, run, brief).
 * Product screens added later call ./edrak.ts. Both talk to the FastAPI backend only.
 */
import type {
  AnalysisDetail,
  AnalysisForm,
  AnalysisSummary,
  Brief,
  CompanyDocument,
  CompanyFields,
  CompanySetup,
  RunView,
  Session,
} from "../types/app";

export type ApiErrorCode =
  | "invalid_credentials"
  | "email_taken"
  | "not_signed_in"
  | "not_found"
  | "wrong_state";

export class ApiError extends Error {
  constructor(
    readonly code: ApiErrorCode,
    message: string = code,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface EdrakApi {
  /* sign in / create account */
  getSession(): Promise<Session | null>;
  signIn(email: string, password: string): Promise<Session>;
  createAccount(email: string, password: string): Promise<Session>;
  signOut(): Promise<void>;

  /* company setup */
  getCompany(): Promise<CompanySetup>;
  saveCompany(fields: CompanyFields): Promise<CompanySetup>;
  uploadDocuments(files: File[]): Promise<CompanyDocument[]>;
  /** Current status of every document. Called while any file is still indexing. */
  listDocuments(): Promise<CompanyDocument[]>;
  removeDocument(documentId: string): Promise<void>;

  /* analyses */
  listAnalyses(): Promise<AnalysisSummary[]>;
  getAnalysis(analysisId: string): Promise<AnalysisDetail>;
  /** Writes the request and has the Supervisor draft a plan. Reuses `analysisId` when given. */
  draftPlan(form: AnalysisForm, analysisId?: string): Promise<AnalysisDetail>;
  /** Approving starts the run. Nothing runs before this. */
  approvePlan(analysisId: string): Promise<void>;
  /** Sends the analysis back to the form with the reason. */
  rejectPlan(analysisId: string, reason: string): Promise<void>;
  /** Starts again from the same request, with a new plan to review. */
  rerun(analysisId: string): Promise<{ analysis_id: string }>;

  /* live run */
  getRun(analysisId: string): Promise<RunView>;
  cancelRun(analysisId: string): Promise<void>;

  /* brief */
  getBrief(briefId: string): Promise<Brief>;
}

import { httpApi } from "./httpApi";

export const api: EdrakApi = httpApi;
