/**
 * Real HTTP client implementing EdrakApi for connecting to the FastAPI backend.
 */
import { ApiError, type EdrakApi } from "./api";
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

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";

async function request<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${BASE_URL}${endpoint}`;
  const headers = new Headers(options.headers || {});

  if (!(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(url, {
    ...options,
    headers,
    credentials: "include",
  });

  if (!res.ok) {
    let errorDetail = "";
    try {
      const errorJson = await res.json();
      errorDetail = errorJson.detail || "";
    } catch {
      errorDetail = res.statusText;
    }

    if (res.status === 401) {
      if (errorDetail === "invalid_credentials") {
        throw new ApiError("invalid_credentials", "Invalid email or password");
      }
      throw new ApiError("not_signed_in", errorDetail || "Not signed in");
    }
    if (res.status === 404) {
      throw new ApiError("not_found", errorDetail || "Not found");
    }
    if (res.status === 409) {
      throw new ApiError("email_taken", errorDetail || "Email taken");
    }
    throw new ApiError("wrong_state", errorDetail || `Request failed with ${res.status}`);
  }

  return (await res.json()) as T;
}

export class HttpApi implements EdrakApi {
  /* ------------------------------------------------------------------ session */

  async getSession(): Promise<Session | null> {
    try {
      return await request<Session>("/api/auth/session", { method: "POST" });
    } catch (err) {
      if (err instanceof ApiError && err.code === "not_signed_in") {
        return null;
      }
      throw err;
    }
  }

  async signIn(email: string, password: string): Promise<Session> {
    return await request<Session>("/api/auth/sign-in", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  }

  async createAccount(email: string, password: string): Promise<Session> {
    return await request<Session>("/api/auth/create-account", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  }

  async signOut(): Promise<void> {
    await request<void>("/api/auth/sign-out", { method: "POST" });
  }

  /* ------------------------------------------------------------ company setup */

  async getCompany(): Promise<CompanySetup> {
    return await request<CompanySetup>("/api/company", { method: "GET" });
  }

  async saveCompany(fields: CompanyFields): Promise<CompanySetup> {
    return await request<CompanySetup>("/api/company", {
      method: "PUT",
      body: JSON.stringify(fields),
    });
  }

  async uploadDocuments(files: File[]): Promise<CompanyDocument[]> {
    const formData = new FormData();
    for (const f of files) {
      formData.append("files", f);
    }
    return await request<CompanyDocument[]>("/api/company/documents", {
      method: "POST",
      body: formData,
    });
  }

  async listDocuments(): Promise<CompanyDocument[]> {
    return await request<CompanyDocument[]>("/api/company/documents", {
      method: "GET",
    });
  }

  async removeDocument(documentId: string): Promise<void> {
    await request<void>(`/api/company/documents/${encodeURIComponent(documentId)}`, {
      method: "DELETE",
    });
  }

  /* ---------------------------------------------------------------- analyses */

  async listAnalyses(): Promise<AnalysisSummary[]> {
    return await request<AnalysisSummary[]>("/api/analyses", {
      method: "GET",
    });
  }

  async getAnalysis(analysisId: string): Promise<AnalysisDetail> {
    return await request<AnalysisDetail>(
      `/api/analyses/${encodeURIComponent(analysisId)}`,
      { method: "GET" },
    );
  }

  async draftPlan(form: AnalysisForm, analysisId?: string): Promise<AnalysisDetail> {
    return await request<AnalysisDetail>("/api/analyses/draft", {
      method: "POST",
      body: JSON.stringify({ form, analysis_id: analysisId }),
    });
  }

  async approvePlan(analysisId: string): Promise<void> {
    await request<void>(`/api/analyses/${encodeURIComponent(analysisId)}/approve`, {
      method: "POST",
    });
  }

  async rejectPlan(analysisId: string, reason: string): Promise<void> {
    await request<void>(`/api/analyses/${encodeURIComponent(analysisId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
  }

  async rerun(analysisId: string): Promise<{ analysis_id: string }> {
    return await request<{ analysis_id: string }>(
      `/api/analyses/${encodeURIComponent(analysisId)}/rerun`,
      { method: "POST" },
    );
  }

  /* ---------------------------------------------------------------- live run */

  async getRun(analysisId: string): Promise<RunView> {
    return await request<RunView>(
      `/api/analyses/${encodeURIComponent(analysisId)}/run`,
      { method: "GET" },
    );
  }

  async cancelRun(analysisId: string): Promise<void> {
    await request<void>(`/api/analyses/${encodeURIComponent(analysisId)}/cancel`, {
      method: "POST",
    });
  }

  /* ------------------------------------------------------------------- brief */

  async getBrief(briefId: string): Promise<Brief> {
    return await request<Brief>(
      `/api/briefs/${encodeURIComponent(briefId)}`,
      { method: "GET" },
    );
  }
}

export const httpApi = new HttpApi();
