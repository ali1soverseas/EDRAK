/** The typed brief (document version 3): cards filled from stored metrics, and the action plan. */

export type CardStatus = "verified" | "thin" | "conflict" | "gap" | "fallback";
export type CardComponent = "kpi" | "ladder" | "segmented" | "line" | "timeline" | "text" | "table" | "gap";

export interface Comparator { kind: "company" | "prior"; label: string; value: string; direction: number; uid: string; formula?: string }
export interface RangeInfo { low: string; high: string; sources: { uid: string; value: string; refs: number[] }[] }

export interface BriefCard {
  card_key: string;
  section: string;
  component: CardComponent;
  metric_id: string | null;
  entity: string;
  title: string;
  headline: string | null;
  why_visual: string | null;
  status: CardStatus;
  caveat: string | null;
  slots: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  refs: number[];
  metric_uids: string[];
  derived: boolean;
  blocking?: boolean;
  version?: number;
  origin?: "pipeline" | "challenge";
  reads_as?: string | null;
  definition?: string;
}

export interface MetricRow {
  uid: string; metric_id: string | null; entity: string; value: number | null; value_text: string; unit: string | null;
  period: string | null; label: string | null; status: "verified" | "thin" | "conflict"; scope: string; sample_size: number | null;
  derived: boolean | number; formula: string | null; source_span: string | null; display_no: number | null; inputs: string[]; worker: string | null;
}

export interface KpiItem {
  card_key: string; metric_id: string; label: { en: string; ar?: string }; entity: string; value: string; status: CardStatus;
  comparator: Comparator | null; uid: string | null;
  period?: string | null; reads_as?: string | null; definition?: string;
}

export interface SectionInfo { lens: string; label: string; weight: number; cards: string[]; gap_cards: string[]; evidence_count: number; collapsed: boolean }
export interface GapItem { card_key: string; metric_id: string; entity: string; title: string; blocking: boolean; reason: string; explanation: string }
export interface SignalView {
  signal_id: string; title: string; signal: string; interpretation: string | null; why_it_matters: string | null; refs: number[]; domains: string[];
  type?: string | null; urgency?: string | null; emphasis?: number; confidence?: number | null; evidence_gaps?: string[];
  sides?: { value: string; uid: string; ref: number | null }[];
}

export interface OptionItem { text: string; how_to_capture?: string; likelihood?: string; impact?: string; early_warning?: string; mitigation?: string; metric_uids?: string[]; evidence_refs?: number[] }
export interface BriefOption {
  option_id: string; key: string; title: string; thesis: string; description: string; what_we_would_do: string[]; trade_offs: string[];
  opportunities: OptionItem[]; risks: OptionItem[]; would_change_if: string; cost_of_waiting?: string;
  supporting_signal_ids: string[]; metric_uids?: string[]; evidence_refs?: number[]; guardrail?: string;
}

export interface BriefEvidenceItem {
  display_no: number; worker: string; reliability: number; note: string; origin: string;
  evidence: { source_title?: string; source_url?: string; publisher?: string; extracted_fact: string; excerpt?: string; retrieved_at?: string };
}

export interface BriefV3 {
  brief_id: string; brief_no: number; analysis_id: string; analysis_title: string; title: string; question: string; use_case: string;
  lifecycle: string; evidence_status: string; document_version: number; created_at: string; partial: boolean;
  headline: string; summary_text: string;
  kpis: KpiItem[];
  coverage: { score: number | null; met: number; required: number; items: { metric_id: string; entity: string; met: boolean }[] };
  gaps_panel: { gaps: GapItem[]; collapsed_lenses: string[] };
  sections: SectionInfo[];
  cards: Record<string, BriefCard>;
  metrics: Record<string, MetricRow>;
  conflicts: { metric_id: string; entity: string; low: number; high: number }[];
  signals_view: SignalView[];
  evidence: BriefEvidenceItem[];
  options: BriefOption[];
  template: { use_case: string; kpi_strip: string[]; required: string[]; metrics_theme?: { title: string; subtitle: string }; decision_framing?: string };
  verification?: { control_summary?: { missing_information: string[]; conflicts: string[] } };
  deck?: Deck;
  counts?: { findings: number; verified: number; sources: number; workers: number };
  summary?: { limits?: string | null };
}

/* ------------------------------------------------------------ the five slides */

export interface KeyFinding { text: string; lens: string; finding_id: string; refs: number[]; reliability: number }
export interface ComparisonCell { entity: string; value: string; label: string | null; period: string | null; uid: string; ref: number | null; status: string }
export interface ComparisonRow { metric_id: string; name: { en: string; ar?: string }; lens?: string; reads_as?: string | null; definition?: string; ours: ComparisonCell[]; theirs: ComparisonCell[]; comparable: boolean; note: string | null }
export interface LensCoverage { lens: string; label: string; sources: number; findings: number; verified: number; required: number; met: number; failed: boolean; collapsed: boolean }
export interface DeckGap { kind: "figure" | "research"; title: string; reason: string | null; explanation: string | null; blocking: boolean; card_key: string | null; action: "rerun" | "provide_data"; lens?: string }

export interface SummarySlideData {
  id: "summary"; title: string; headline: string; question: string; use_case: string; key_findings: KeyFinding[];
  top_signals: string[]; kpis: string[]; coverage: BriefV3["coverage"]; limits: string | null;
  counts?: BriefV3["counts"]; unmet: string[];
}
export interface MetricsSlideData {
  id: "business_metrics"; title: string; subtitle: string; kpis: string[]; cards: string[]; gap_cards: string[];
  comparison: ComparisonRow[]; company: string; collapsed: boolean; collapsed_reason: string | null;
}
export interface SignalsSlideData { id: "signals"; title: string; items: string[]; lenses: { lens: string; label: string }[]; emphasis: Record<string, number> }
export interface DecisionsSlideData { id: "decisions"; title: string; framing: string; options: string[]; priorities_known: boolean }
export interface EvidenceSlideData {
  id: "evidence_gaps"; title: string; coverage: BriefV3["coverage"]; by_lens: LensCoverage[];
  source_mix: { total: number; internal: number; external: number; by_reliability: Record<string, number>; by_kind: Record<string, number> };
  conflicts: string[]; gaps: DeckGap[]; collapsed: string[];
}
export type DeckSlide = SummarySlideData | MetricsSlideData | SignalsSlideData | DecisionsSlideData | EvidenceSlideData;
export interface Deck { version: number; order: DeckSlide["id"][]; slides: DeckSlide[] }

/* ------------------------------------------------------------ action plan */

export type Level = "low" | "moderate" | "high";
export type Origin = "generated" | "user";
export interface PlanAction {
  id: string; title: string; description: string; owner_role: string; start_week: number; duration_weeks: number; depends_on: string[];
  deliverable: string; success_measure: string; validation?: string; signal_ids?: string[]; evidence_refs?: number[]; origin?: Origin;
}
export interface Workstream { id: string; name: string; objective: string; actions: PlanAction[] }
export interface Milestone { id: string; week: number; title: string; go_criteria: string; kill_criteria: string }
export interface PlanKpi { id: string; name: string; metric_id: string | null; entity: string; new_measure: boolean; baseline_uid: string | null; baseline: string; direction: "increase" | "decrease" | "maintain"; target: string; target_status: "proposed" | "accepted" | "edited"; cadence: string }
export interface PlanRisk { id: string; text: string; likelihood: Level; impact: Level; early_warning: string; mitigation: string; mitigation_action_ids: string[]; owner_role: string; evidence_refs: number[]; signal_ids?: string[]; origin?: Origin }
export interface PlanOpportunity { id: string; text: string; how_to_capture: string; action_ids: string[]; evidence_refs: number[]; signal_ids?: string[]; origin?: Origin }
export interface ActionPlanDoc {
  objective: string;
  chosen_option: { key: string; title: string; why: string; signal_ids: string[]; evidence_refs: number[] };
  alternatives_not_chosen: { key: string; title: string; why_not: string }[];
  assumptions: { id: string; text: string; metric_uid: string | null; gap: string }[];
  open_questions: { id: string; text: string; close_by_week: number | null }[];
  workstreams: Workstream[]; milestones: Milestone[]; kpis: PlanKpi[]; risks: PlanRisk[]; opportunities: PlanOpportunity[];
  resources: string[]; review_date: string | null; review_cadence: string; total_weeks: number; notes: string[]; edited?: boolean;
}
export interface ActionPlanRow {
  id: string; brief_id: string; option_key: string; version: number; status: "generating" | "draft" | "approved" | "superseded" | "failed";
  state?: "generating" | "draft" | "edited" | "approved" | "superseded" | "failed";
  note: string | null; error: string | null; created_at: string; approved_at: string | null; document: ActionPlanDoc | Record<string, never>;
  issues?: { errors: string[]; warnings: string[] };
  versions?: { id: string; version: number; status: string; state?: string; created_at: string; note: string | null; approved_at?: string | null }[];
}

export interface ChallengeResult {
  changed: boolean; reason?: string; message?: string; version?: number; conflict?: boolean; card?: BriefCard;
}
export interface ChatReply { message_id: string; content: string; cited: number[]; label: "brief" | "web_unverified" | "not_covered"; metric_uids: string[]; notes: string[]; proposal?: string | null }
