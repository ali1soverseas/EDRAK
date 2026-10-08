import { beforeEach, describe, expect, it } from "vitest";

/** A minimal localStorage, since the tests run in Node. */
function installStorage() {
  const store = new Map<string, string>();
  const localStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  };
  Object.assign(globalThis, { window: { localStorage, location: { reload: () => undefined } } });
}

installStorage();

// Imported after the storage exists, because the mock reads it when first used.
const { mockApi } = await import("./mockApi");
const { resetDb } = await import("./db");
const { ApiError } = await import("../api");
const { DEMO_EMAIL, DEMO_PASSWORD } = await import("./seed");
const { defaultForm } = await import("../../lib/form");

beforeEach(() => {
  resetDb();
});

describe("sign in", () => {
  it("accepts the demo account and refuses a wrong password", async () => {
    await expect(mockApi.signIn(DEMO_EMAIL, "nope")).rejects.toMatchObject({ code: "invalid_credentials" });
    const session = await mockApi.signIn(DEMO_EMAIL, DEMO_PASSWORD);
    expect(session.user.name).toBe("Layla Hassan");
    expect((await mockApi.getSession())?.workspace.name).toBe("Nile Ledger");
    await mockApi.signOut();
    expect(await mockApi.getSession()).toBeNull();
  });

  it("creates an account in a new, empty workspace", async () => {
    const session = await mockApi.createAccount("omar.farouk@acme.example", "a-long-password");
    expect(session.user.name).toBe("Omar Farouk");
    expect(await mockApi.listAnalyses()).toEqual([]);
    expect((await mockApi.getCompany()).completed).toBe(false);
    await expect(mockApi.createAccount("omar.farouk@acme.example", "another-password")).rejects.toMatchObject({ code: "email_taken" });
  });

  it("keeps private data behind a session", async () => {
    await expect(mockApi.listAnalyses()).rejects.toBeInstanceOf(ApiError);
  });
});

describe("plans and runs", () => {
  beforeEach(async () => {
    await mockApi.signIn(DEMO_EMAIL, DEMO_PASSWORD);
  });

  it("lists the six seeded analyses, newest first", async () => {
    const rows = await mockApi.listAnalyses();
    expect(rows).toHaveLength(6);
    const times = rows.map((row) => Date.parse(row.updated_at));
    expect(times).toEqual([...times].sort((a, b) => b - a));
    const statuses = rows.map((row) => row.status).sort();
    expect(statuses).toEqual(["awaiting_approval", "completed", "draft", "failed", "partial", "running"]);
  });

  it("drafts one task per allowed worker, and nothing runs before approval", async () => {
    const form = { ...defaultForm("product_launch"), goal: "Should we?", market: "Egypt", sources: { internal_files: true, competitor_web: true, news_open_data: false, reviews_social: false } };
    const detail = await mockApi.draftPlan(form);
    expect(detail.status).toBe("awaiting_approval");
    expect(detail.plan?.tasks.map((task) => task.worker)).toEqual(["internal_intelligence", "competitor_intelligence"]);
    await expect(mockApi.getRun(detail.analysis_id)).rejects.toMatchObject({ code: "wrong_state" });
  });

  it("starts the run only when the plan is approved", async () => {
    const form = { ...defaultForm("competitive_intelligence"), goal: "What are they doing?" };
    const { analysis_id } = await mockApi.draftPlan(form);
    await mockApi.approvePlan(analysis_id);
    const run = await mockApi.getRun(analysis_id);
    expect(run.state).toBe("running");
    expect(run.tasks).toHaveLength(4);
    await expect(mockApi.approvePlan(analysis_id)).rejects.toMatchObject({ code: "wrong_state" });
  });

  it("sends a rejected plan back as a draft with the reason, and drafts again from it", async () => {
    const form = { ...defaultForm("product_launch"), goal: "Should we?" };
    const { analysis_id } = await mockApi.draftPlan(form);
    await mockApi.rejectPlan(analysis_id, "  Leave out reviews.  ");
    const draft = await mockApi.getAnalysis(analysis_id);
    expect(draft.status).toBe("draft");
    expect(draft.plan).toBeNull();
    expect(draft.rejection_reason).toBe("Leave out reviews.");
    const again = await mockApi.draftPlan({ ...draft.form, goal: "Should we, narrowly?" }, analysis_id);
    expect(again.analysis_id).toBe(analysis_id);
    expect(again.status).toBe("awaiting_approval");
  });

  it("cancels a running analysis", async () => {
    await mockApi.cancelRun("an-fintech-live");
    expect((await mockApi.getRun("an-fintech-live")).state).toBe("cancelled");
    const row = (await mockApi.listAnalyses()).find((candidate) => candidate.analysis_id === "an-fintech-live");
    expect(row?.status).toBe("cancelled");
  });

  it("serves briefs only for finished runs", async () => {
    await expect(mockApi.getBrief("brief-an-fintech-live")).rejects.toMatchObject({ code: "not_found" });
    const brief = await mockApi.getBrief("brief-an-fintech-w39");
    expect(brief.partial).toBe(false);
    expect(brief.lenses).toHaveLength(4);
    const partial = await mockApi.getBrief("brief-an-saudi");
    expect(partial.partial).toBe(true);
    expect(partial.lenses.filter((lens) => lens.failed)).toHaveLength(1);
  });

  it("re-runs a failed analysis from the same request, as a new plan", async () => {
    const { analysis_id } = await mockApi.rerun("an-retail");
    expect(analysis_id).not.toBe("an-retail");
    expect((await mockApi.getAnalysis(analysis_id)).status).toBe("awaiting_approval");
  });
});
