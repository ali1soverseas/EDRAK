// =============================================================================
// EDRAK knowledge graph — Neo4j schema
// Every label and relationship below is mapped from a class in the project:
//   contracts/request.py, task.py, result.py, evidence.py, verification.py,
//   metrics.py, CrossSignal.py, DecisionAnalysis.py, metrics/registry.py,
//   agents/customer_trends/schemas/evidence.py and db/schema.sql, plus the worker
//   metadata documented in agents/competitor_intelligence/nodes.py (build_worker_result),
//   agents/customer_trends/runner.py and connectors/tri_modal_retriever.py.
// Run once against an empty database (Neo4j 5.x). All statements are idempotent.
// =============================================================================


// -----------------------------------------------------------------------------
// 1. CONSTRAINTS (one unique key per node label)
// -----------------------------------------------------------------------------

// Workspace layer (db/schema.sql)
CREATE CONSTRAINT workspace_id     IF NOT EXISTS FOR (n:Workspace)          REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT profile_snapshot_id IF NOT EXISTS FOR (n:ProfileSnapshot) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT user_id          IF NOT EXISTS FOR (n:User)               REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT monitor_id       IF NOT EXISTS FOR (n:Monitor)            REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT document_id      IF NOT EXISTS FOR (n:Document)           REQUIRE n.id IS UNIQUE;

// Request and plan (contracts/request.py, task.py, metrics.py)
CREATE CONSTRAINT request_id       IF NOT EXISTS FOR (n:BusinessRequest)    REQUIRE n.request_id IS UNIQUE;
CREATE CONSTRAINT plan_id          IF NOT EXISTS FOR (n:ResearchPlan)       REQUIRE n.plan_id IS UNIQUE;
CREATE CONSTRAINT task_id          IF NOT EXISTS FOR (n:ResearchTask)       REQUIRE n.task_id IS UNIQUE;
CREATE CONSTRAINT worker_type      IF NOT EXISTS FOR (n:Worker)             REQUIRE n.type IS UNIQUE;
CREATE CONSTRAINT requirement_key  IF NOT EXISTS FOR (n:MetricRequirement)  REQUIRE n.key IS UNIQUE;   // task_id|metric_id|entity

// Research output (contracts/result.py, evidence.py)
CREATE CONSTRAINT result_key       IF NOT EXISTS FOR (n:WorkerResult)       REQUIRE n.key IS UNIQUE;   // task_id|attempt
CREATE CONSTRAINT finding_id       IF NOT EXISTS FOR (n:Finding)            REQUIRE n.finding_id IS UNIQUE;
CREATE CONSTRAINT evidence_id      IF NOT EXISTS FOR (n:Evidence)           REQUIRE n.evidence_id IS UNIQUE;
CREATE CONSTRAINT source_host      IF NOT EXISTS FOR (n:Source)             REQUIRE n.host IS UNIQUE;
CREATE CONSTRAINT conflict_key     IF NOT EXISTS FOR (n:Conflict)           REQUIRE n.key IS UNIQUE;   // result_key|finding_id|n
CREATE CONSTRAINT gap_id           IF NOT EXISTS FOR (n:InformationGap)     REQUIRE n.id IS UNIQUE;

// Market entities (CompanyProfile, CompetitorRef, BusinessContext, entities table)
CREATE CONSTRAINT company_id       IF NOT EXISTS FOR (n:Company)            REQUIRE n.id IS UNIQUE;    // canonical slug
CREATE CONSTRAINT product_id       IF NOT EXISTS FOR (n:Product)            REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT market_id        IF NOT EXISTS FOR (n:Market)             REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT topic_id         IF NOT EXISTS FOR (n:Topic)              REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT segment_id       IF NOT EXISTS FOR (n:CustomerSegment)    REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT account_url      IF NOT EXISTS FOR (n:SocialAccount)      REQUIRE n.url IS UNIQUE;
CREATE CONSTRAINT theme_id         IF NOT EXISTS FOR (n:Theme)              REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT feature_id       IF NOT EXISTS FOR (n:Feature)            REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT pricing_plan_id  IF NOT EXISTS FOR (n:PricingPlan)        REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT regulation_id    IF NOT EXISTS FOR (n:Regulation)         REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT platform_id      IF NOT EXISTS FOR (n:Platform)           REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT industry_id      IF NOT EXISTS FOR (n:Industry)           REQUIRE n.id IS UNIQUE;    // company_profiles.industry, entities.sector
CREATE CONSTRAINT search_term_id   IF NOT EXISTS FOR (n:SearchTerm)         REQUIRE n.id IS UNIQUE;    // slug of the keyword

// Company systems the internal worker reads (CompanyProfile.db_config / api_config, tri_modal_retriever.py)
CREATE CONSTRAINT data_source_id   IF NOT EXISTS FOR (n:DataSource)         REQUIRE n.id IS UNIQUE;    // stable_id(workspace|channel|db_id or api_id)
CREATE CONSTRAINT data_asset_id    IF NOT EXISTS FOR (n:DataAsset)          REQUIRE n.id IS UNIQUE;    // stable_id(source|kind|table or endpoint)

// Run-scoped observations from worker metadata (competitor comparison matrix, customer themes and trends)
CREATE CONSTRAINT assessment_key   IF NOT EXISTS FOR (n:Assessment)         REQUIRE n.key IS UNIQUE;   // stable_id(result_key|dimension|company)
CREATE CONSTRAINT theme_obs_key    IF NOT EXISTS FOR (n:ThemeObservation)   REQUIRE n.key IS UNIQUE;   // stable_id(result_key|label)
CREATE CONSTRAINT trend_key        IF NOT EXISTS FOR (n:TrendSeries)        REQUIRE n.key IS UNIQUE;   // stable_id(result_key|term|geo|timeframe)

// Metrics (metrics/registry.py MetricDef, contracts/metrics.py MetricObservation)
CREATE CONSTRAINT metric_id        IF NOT EXISTS FOR (n:Metric)             REQUIRE n.metric_id IS UNIQUE;
CREATE CONSTRAINT observation_key  IF NOT EXISTS FOR (n:MetricObservation)  REQUIRE n.key IS UNIQUE;   // evidence_id|metric_id|entity|label

// Verification, cross-signal, decision (verification.py, CrossSignal.py, DecisionAnalysis.py)
CREATE CONSTRAINT verification_id  IF NOT EXISTS FOR (n:VerificationResult) REQUIRE n.id IS UNIQUE;   // research_run_id|completed_at
CREATE CONSTRAINT signal_id        IF NOT EXISTS FOR (n:CrossSignal)        REQUIRE n.signal_id IS UNIQUE;
CREATE CONSTRAINT analysis_id      IF NOT EXISTS FOR (n:DecisionAnalysis)   REQUIRE n.analysis_id IS UNIQUE;
CREATE CONSTRAINT opportunity_id   IF NOT EXISTS FOR (n:Opportunity)        REQUIRE n.opportunity_id IS UNIQUE;
CREATE CONSTRAINT risk_id          IF NOT EXISTS FOR (n:Risk)               REQUIRE n.risk_id IS UNIQUE;
CREATE CONSTRAINT action_id        IF NOT EXISTS FOR (n:RecommendedAction)  REQUIRE n.action_id IS UNIQUE;
CREATE CONSTRAINT option_key       IF NOT EXISTS FOR (n:DecisionOption)     REQUIRE n.key IS UNIQUE;   // analysis_id|key
CREATE CONSTRAINT recommendation   IF NOT EXISTS FOR (n:Recommendation)     REQUIRE n.key IS UNIQUE;   // analysis_id|n

// Briefs and monitoring (db/schema.sql)
CREATE CONSTRAINT brief_id         IF NOT EXISTS FOR (n:Brief)              REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT decision_id      IF NOT EXISTS FOR (n:Decision)           REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT change_id        IF NOT EXISTS FOR (n:ChangeEvent)        REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT alert_id         IF NOT EXISTS FOR (n:Alert)              REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT timeline_id      IF NOT EXISTS FOR (n:TimelineEvent)      REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT annotation_id    IF NOT EXISTS FOR (n:Annotation)         REQUIRE n.id IS UNIQUE;


// -----------------------------------------------------------------------------
// 2. INDEXES
// -----------------------------------------------------------------------------
CREATE INDEX request_status   IF NOT EXISTS FOR (n:BusinessRequest)   ON (n.status, n.use_case);
CREATE INDEX finding_category IF NOT EXISTS FOR (n:Finding)           ON (n.category);
CREATE INDEX evidence_type    IF NOT EXISTS FOR (n:Evidence)          ON (n.source_type, n.is_synthetic);
CREATE INDEX evidence_quality IF NOT EXISTS FOR (n:Evidence)          ON (n.quality);
CREATE INDEX obs_scope        IF NOT EXISTS FOR (n:MetricObservation) ON (n.scope, n.status);
CREATE INDEX signal_type      IF NOT EXISTS FOR (n:CrossSignal)       ON (n.signal_type, n.urgency);
CREATE INDEX gap_status       IF NOT EXISTS FOR (n:InformationGap)    ON (n.status);
CREATE INDEX finding_ws       IF NOT EXISTS FOR (n:Finding)           ON (n.workspace_id, n.verification_status);
CREATE INDEX finding_request  IF NOT EXISTS FOR (n:Finding)           ON (n.request_id);
CREATE INDEX evidence_url     IF NOT EXISTS FOR (n:Evidence)          ON (n.source_url);
CREATE INDEX signal_ws        IF NOT EXISTS FOR (n:CrossSignal)       ON (n.workspace_id);
CREATE INDEX gap_ws           IF NOT EXISTS FOR (n:InformationGap)    ON (n.workspace_id, n.status);
CREATE INDEX obs_ws           IF NOT EXISTS FOR (n:MetricObservation) ON (n.workspace_id, n.metric_id);
CREATE INDEX assessment_ws    IF NOT EXISTS FOR (n:Assessment)        ON (n.workspace_id, n.request_id);
CREATE INDEX theme_obs_ws     IF NOT EXISTS FOR (n:ThemeObservation)  ON (n.workspace_id, n.request_id);
CREATE INDEX trend_ws         IF NOT EXISTS FOR (n:TrendSeries)       ON (n.workspace_id, n.keyword);
CREATE INDEX data_source_ws   IF NOT EXISTS FOR (n:DataSource)        ON (n.workspace_id);
// Adding a label to an existing full-text index needs DROP INDEX entity_names; then this file again.
CREATE FULLTEXT INDEX entity_names IF NOT EXISTS
  FOR (n:Company|Product|Market|Topic|Feature|PricingPlan|Regulation|Theme|Platform|Industry) ON EACH [n.name, n.aliases];
CREATE FULLTEXT INDEX claim_text IF NOT EXISTS
  FOR (n:Finding|Evidence) ON EACH [n.statement, n.extracted_fact];


// =============================================================================
// 3. NODE CATALOG  (label → properties → source class)
// =============================================================================
//
// --- Workspace layer -----------------------------------------------------------
// (:Workspace {id, name, default_language, time_zone})                    workspaces, workspace_settings
// (:User {id, full_name, language})                                       users, user_preferences
// (:Monitor {id, cadence})                                                monitors
// (:Document {id, title, doc_type})                                       documents (internal RAG files)
//
// --- Request and plan ----------------------------------------------------------
// (:BusinessRequest {request_id, goal, use_case, trigger, time_window_days,
//    constraints[], focus_areas[], status, launched_by, created_at, completed_at})
//                                                                         BusinessRequest + BusinessContext + runs
//    use_case ∈ competitive_intelligence | market_entry_expansion | product_launch
// (:ResearchPlan {plan_id, version, status, rationale, created_at, approved_at, auto_approved})
//                                                                         ResearchPlan + research_plans
// (:ResearchTask {task_id, goal, focus, attempt, position, origin})       ResearchTask
// (:Worker {type})                                                        WorkerType (4 fixed nodes)
//    type ∈ internal_intelligence | competitor_intelligence | market_intelligence | customer_trends
// (:MetricRequirement {key, metric_id, entity, required, definition, check_rule,
//    preferred_source_types[], query_hints[], outcome, gap_reason, attempts})
//                                                                         MetricRequirement + RequirementOutcome
//
// --- Research output -----------------------------------------------------------
// (:WorkerResult {key, status, attempt, confidence, started_at, completed_at, error, gaps[]})
//                                                                         WorkerResult (status: WorkerStatus)
// (:Finding {finding_id, statement, category, confidence, limitations[]}) Finding (category: FindingCategory)
// (:Evidence {evidence_id, source_type, source_title, source_url, publisher,
//    extracted_fact, excerpt, retrieved_at, published_at, is_synthetic, quality,
//    platform, language, engagement_total, snippet_only, provider, content_hash})
//                                                                         Evidence + customer_trends EvidenceItem
// (:Source {host, tier, weight, is_first_party})                          verification/quality.py source tiers
// (:Conflict {key, description})                                          Conflict
// (:InformationGap {id, subject, description, searched_note, suggested_action, status})
//                                                                         information_gaps
//
// --- Market entities -----------------------------------------------------------
// (:Company {id, name, aliases[], website, sector, country, notes, summary,
//    discovered_by, discovery_confidence})                                CompanyProfile, CompetitorRef, entities(kind=competitor)
//    The user's own company and competitors share one label; the role is the relationship
//    (Workspace-[:OWNS_PROFILE]->, BusinessRequest-[:COMPARES_AGAINST]->).
// (:Product {id, name})                                                   CompanyProfile.products, BusinessContext.offering
// (:Market {id, name})                                                    BusinessContext.markets, entities(kind=market)
// (:Topic {id, name})                                                     BusinessContext.focus_areas, entities(kind=topic)
// (:CustomerSegment {id, name})                                           BusinessContext.target_customers
// (:SocialAccount {url, platform, verified, found_on_url})                SocialAccount, entity_links
// (:Theme {id, name, aliases[]})                                         catalog: customer themes
// (:Feature {id, name, aliases[], description})                           catalog: product capabilities
// (:PricingPlan {id, name, aliases[], description})                       catalog: plans and tiers (prices live in MetricObservation)
// (:Regulation {id, name, aliases[], description})                        catalog: laws and rules per market
// (:Platform {id, name})                                                  where customer evidence was posted (Evidence.metadata.platform)
// (:Industry {id, name, aliases[], description})                          catalog: sectors (company_profiles.industry, entities.sector)
//    Catalog entities come from a curated list passed to GraphIngestor.ingest_run(catalog=...)
//    (sample_data.json "catalog"), never from model output. The workspace TRACKS them.
// (:SearchTerm {id, name})                                                a keyword whose search interest the customer worker measured
//                                                                         (WorkerResult.metadata.trend_series[].keyword)
//
// --- Company data sources (internal worker, tri-modal retrieval) ---------------
// (:DataSource {id, name, kind, description, allowed_tables[], workspace_id})
//                                                                         DatabaseConnectionConfig (kind sql_database), RESTAPIConfig (kind rest_api)
//    Symbolic ids only. connection_uri, base_url, openapi spec and auth_headers are NEVER written.
// (:DataAsset {id, name, kind})                                           kind table (metadata.table) | endpoint (metadata.endpoint)
//
// --- Run-scoped observations (worker metadata; each carries request_id, workspace_id, worker, observed_at)
// (:Assessment {key, dimension, competitor, relative_to_target, summary, confidence, target_position, takeaway})
//                                                                         competitor metadata.comparison_matrix.dimensions[].cells[]
//    relative_to_target ∈ broader | comparable | narrower | different_approach | not_established
// (:ThemeObservation {key, label, count, share, recent_growth,
//    sentiment_positive, sentiment_neutral, sentiment_negative, sentiment_mixed})
//                                                                         customer metadata.themes[] (ThemeAggregate)
//    The label is model-written, so it never becomes a Theme node; it links to a catalog Theme when it matches one.
// (:TrendSeries {key, keyword, geo, timeframe, granularity, source, dates[], values[],
//    points, first_value, latest_value, peak_value})                       customer metadata.trend_series[] (TrendSeries)
//
// --- Metrics -------------------------------------------------------------------
// (:Metric {metric_id, name, aliases[], definition, unit_kind, period, comparator})
//                                                                         MetricDef (metrics/registry.yaml)
// (:MetricObservation {key, value_text, value, unit, period, label, definition,
//    source_span, scope, sample_size, status, derived, formula, observed_on})
//                                                                         MetricObservation + entity_metric_points
//    scope ∈ official | third_party | internal | public_proxy_for_internal
//    status ∈ verified | thin | conflict | unverified
//
// --- Verification, cross-signal, decision ---------------------------------------
// (:VerificationResult {id, status, summary, completed_at,
//    missing_information[], conflicts[], failures[], next_research_targets[]})
//                                                                         VerificationResult + VerificationDecision + ControlSummary
// (:CrossSignal {signal_id, signal_type, title, signal, interpretation, confidence,
//    evidence_quality, strategic_relevance, implications[], decision_relevance,
//    opportunity_relevance, risk_relevance, affected_business_areas[], decision_areas[],
//    decision_question, urgency, dependencies[], evidence_gaps[]})       CrossSignal
//    signal_type ∈ convergence | dependency | timing | gap | momentum
// (:DecisionAnalysis {analysis_id, status, business_goal, executive_summary,
//    decision_questions[], additional_information_needed[], limitations[], warnings[], created_at})
//                                                                         DecisionAnalysisResult
// (:Opportunity {opportunity_id, title, description, business_rationale, potential_impact,
//    strategic_fit, feasibility, urgency, assumptions[], evidence_gaps[], confidence})
//                                                                         OpportunityAssessment
// (:Risk {risk_id, title, description, business_rationale, potential_consequences[],
//    likelihood, impact, urgency, dependencies[], mitigation_considerations[], evidence_gaps[], confidence})
//                                                                         RiskAssessment
// (:RecommendedAction {action_id, title, description, rationale, intended_outcome, priority,
//    time_horizon, prerequisites[], success_indicators[], confidence, requires_human_approval})
//                                                                         RecommendedAction
// (:DecisionOption {key, title, thesis, what_we_would_do[], trade_offs[], would_change_if, cost_of_waiting})
//                                                                         DecisionOption (OptionOpportunity / OptionRisk kept as list props)
// (:Recommendation {key, question, decision, rationale, actions[], success_criteria[],
//    limitations[], confidence, impact, feasibility})                     QuestionRecommendation + RecommendationDetails
//
// --- Briefs and monitoring -----------------------------------------------------
// (:Brief {id, number, title, use_case, language, summary, evidence_status, lifecycle, generated_at})
// (:Decision {id, option_label, title, rationale, review_on, status, recorded_at})
// (:ChangeEvent {id, change_type, importance, title, detail, created_at})
// (:Alert {id, kind, importance, title, what_changed, why_it_matters, status})
// (:TimelineEvent {id, occurred_on, title, detail})
// (:Annotation {id, kind, body, created_at})        kind ∈ flag_wrong | flag_low_quality | note


// =============================================================================
// 4. RELATIONSHIP CATALOG  (from)-[:TYPE {props}]->(to)
// =============================================================================
//
// --- Workspace ---------------------------------------------------------------
// (User)-[:MEMBER_OF {role}]->(Workspace)
// (Workspace)-[:OWNS_PROFILE]->(Company)                          the user's own company
// (Workspace)-[:TRACKS {since, is_tracked}]->(Company|Market|Topic)   watchlist entities
// (Document)-[:IN_WORKSPACE]->(Workspace)
// (Monitor)-[:IN_WORKSPACE]->(Workspace)
// (Monitor)-[:LAUNCHED]->(BusinessRequest)
//
// --- Request and plan --------------------------------------------------------
// (User)-[:CREATED]->(BusinessRequest)
// (BusinessRequest)-[:IN_WORKSPACE]->(Workspace)
// (BusinessRequest)-[:ABOUT_COMPANY]->(Company)                   company_profile
// (BusinessRequest)-[:COMPARES_AGAINST {discovered_by, discovery_reason, discovery_confidence,
//    overview, overview_caveats[]}]->(Company)                     competitors / targets (+ competitor metadata)
// (BusinessRequest)-[:IN_MARKET]->(Market)
// (BusinessRequest)-[:FOCUSES_ON]->(Topic)
// (BusinessRequest)-[:ASSESSES]->(Product)                        offering
// (BusinessRequest)-[:TARGETS]->(CustomerSegment)
// (BusinessRequest)-[:FOLLOW_UP_OF {reason}]->(BusinessRequest)
// (BusinessRequest)-[:HAS_PLAN]->(ResearchPlan)
// (User)-[:APPROVED {at, auto}]->(ResearchPlan)
// (ResearchPlan)-[:HAS_TASK {position}]->(ResearchTask)
// (ResearchTask)-[:ASSIGNED_TO]->(Worker)
// (ResearchTask)-[:REQUIRES]->(MetricRequirement)
// (MetricRequirement)-[:FOR_METRIC]->(Metric)
// (MetricRequirement)-[:FOR_ENTITY]->(Company|Market|Product)
//
// --- Research output ---------------------------------------------------------
// (ResearchTask)-[:RETURNED]->(WorkerResult)                      one per attempt
// (WorkerResult)-[:PRODUCED_BY]->(Worker)
// (WorkerResult)-[:PRODUCED]->(Finding)
// (WorkerResult)-[:COLLECTED]->(Evidence)
// (WorkerResult)-[:REPORTED_GAP]->(InformationGap)
// (WorkerResult)-[:OBSERVED]->(MetricObservation)
// (Finding)-[:SUPPORTED_BY]->(Evidence)                           EvidenceRelation.SUPPORTS
// (Finding)-[:CONTRADICTED_BY]->(Evidence)                        EvidenceRelation.CONTRADICTS
// (Finding)-[:CONTEXTUALIZED_BY]->(Evidence)                      EvidenceRelation.CONTEXTUALIZES
// (Finding)-[:ABOUT]->(Company|Product|Market|Topic|CustomerSegment)
// (Conflict)-[:ON]->(Finding)
// (Conflict)-[:CITES]->(Evidence)                                 contradicting_evidence_ids
// (Evidence)-[:PUBLISHED_BY]->(Source)
// (Evidence)-[:FROM_DOCUMENT]->(Document)                         internal / synthetic_internal documents only (not SQL / API)
// (Evidence)-[:RETRIEVED_FROM {query, row_count}]->(DataAsset)    metadata.channel ∈ sql_database | rest_api
// (Evidence)-[:MENTIONS]->(Company)                               share of voice
// (Evidence)-[:POSTED_ON]->(Platform)                            Evidence.metadata.platform
// (Evidence)-[:EXPRESSES {stance}]->(Theme)                       evidence text matches a catalog theme
// (InformationGap)-[:FOLLOWED_UP_BY]->(BusinessRequest)
//
// --- Market entities ---------------------------------------------------------
// (Company)-[:OFFERS]->(Product)
// (Company)-[:HAS_ACCOUNT]->(SocialAccount)
// (Company)-[:OWNS_DOMAIN]->(Source)                              website + competitor metadata official_domains;
//                                                                 evidence PUBLISHED_BY that host (or a subdomain) is first-party
// (Company|Product)-[:IN_INDUSTRY]->(Industry)                   catalog "of"
// (Company)-[:OPERATES_IN]->(Market)
// (Company)-[:COMPETES_WITH {category}]->(Company)
// (Product)-[:ALTERNATIVE_TO]->(Product)
// (Company|Product)-[:HAS_FEATURE]->(Feature)                    catalog "of"
// (Company|Product)-[:HAS_PLAN]->(PricingPlan)                   catalog "of"
// (Regulation)-[:APPLIES_IN]->(Market)                           catalog "applies_in"
// (Workspace)-[:TRACKS {since}]->(Product|Feature|PricingPlan|Regulation|Theme|Platform|Industry)
// (Finding)-[:ABOUT]->(Feature|PricingPlan|Regulation|Theme|Industry)   whole-word match on name or aliases
// Rule: domain edges are written only when a verified Finding is ABOUT both ends,
//       and carry {finding_ids[], first_seen, last_seen}.
//
// --- Metrics -----------------------------------------------------------------
// (MetricObservation)-[:MEASURES]->(Metric)
// (MetricObservation)-[:OF]->(Company|Market|Product|PricingPlan|Feature|CustomerSegment)
// (MetricObservation)-[:OF_ITEM]->(Product|PricingPlan|Feature)   MetricObservation.label names a catalog item ("B200")
// (MetricObservation)-[:EXTRACTED_FROM]->(Evidence)               source_span must be in the evidence text
// (MetricRequirement)-[:FULFILLED_BY]->(MetricObservation)        outcome = found
// (Metric)-[:OWNED_BY]->(Worker)                                  MetricDef.owner
//
// --- Company data sources ----------------------------------------------------
// (Workspace)-[:CONNECTS]->(DataSource)
// (DataAsset)-[:IN_SOURCE]->(DataSource)
//
// --- Competitor comparison (competitor metadata) -----------------------------
// (WorkerResult)-[:ASSESSED]->(Assessment)
// (Assessment)-[:OF]->(Company)                                   the competitor
// (Assessment)-[:RELATIVE_TO]->(Company)                          the pilot
// (Assessment)-[:ON_DIMENSION]->(Topic)                           comparison dimension (= focus area slug when they match)
// (Assessment)-[:CITES]->(Evidence)                               cells[].evidence_ids
//
// --- Customer voice (customer metadata) --------------------------------------
// (WorkerResult)-[:OBSERVED_THEME]->(ThemeObservation)-[:OF_THEME]->(Theme)
// (WorkerResult)-[:OBSERVED_TREND]->(TrendSeries)-[:FOR_TERM]->(SearchTerm)
// (SearchTerm)-[:REFERS_TO]->(Company|Product|Market|...)         keyword resolves to a known entity
// (WorkerResult)-[:MEASURED_STANCE {positive, neutral, negative, mixed, total, request_id}]->(Company|Product|...)
//                                                                 metadata.stance_counts, counted in code
//
// --- Verification ------------------------------------------------------------
// (BusinessRequest)-[:VERIFIED_BY]->(VerificationResult)
// (VerificationResult)-[:JUDGED {status, evidence_quality, confidence,
//    contradictions[], missing_information[]}]->(Finding)        FindingVerdict
// (VerificationResult)-[:REQUESTED_RETRY {reason}]->(Worker)      TargetedAction
//
// --- Cross-signal ------------------------------------------------------------
// (BusinessRequest)-[:HAS_SIGNAL]->(CrossSignal)
// (CrossSignal)-[:BASED_ON {relevance}]->(Finding)                SignalEvidence, min 2, verified only
// (CrossSignal)-[:SPANS]->(Worker)                                domains_involved
//
// --- Decision ----------------------------------------------------------------
// (BusinessRequest)-[:HAS_ANALYSIS]->(DecisionAnalysis)
// (DecisionAnalysis)-[:IDENTIFIED]->(Opportunity)
// (DecisionAnalysis)-[:IDENTIFIED]->(Risk)
// (DecisionAnalysis)-[:RECOMMENDS]->(RecommendedAction)
// (DecisionAnalysis)-[:PRESENTS]->(DecisionOption)
// (DecisionAnalysis)-[:ANSWERS]->(Recommendation)
// (DecisionAnalysis)-[:PRIORITIZED {priority, strategic_fit, impact, urgency,
//    feasibility, rationale, key_uncertainties[]}]->(Opportunity|Risk|RecommendedAction)   PriorityAssessment
// (Opportunity|Risk|RecommendedAction|DecisionOption|Recommendation)-[:SUPPORTED_BY_SIGNAL]->(CrossSignal)
// (RecommendedAction)-[:ADDRESSES]->(Opportunity)                 related_opportunity_ids
// (RecommendedAction)-[:MITIGATES]->(Risk)                        related_risk_ids
// (DecisionOption)-[:CITES_METRIC {entity}]->(Metric)             MetricRef
//
// --- Briefs and monitoring ---------------------------------------------------
// (Brief)-[:SUMMARIZES]->(BusinessRequest)
// (Brief)-[:COVERS]->(Company|Market|Topic)                       brief_entities
// (User)-[:REQUESTED]->(Brief)
// (Decision)-[:BASED_ON]->(Brief)
// (Decision)-[:CHOSE]->(DecisionOption)                           option_label
// (Decision)-[:OWNED_BY]->(User)
// (Decision)-[:SUPERSEDES]->(Decision)
// (ChangeEvent)-[:DETECTED_IN]->(BusinessRequest)
// (ChangeEvent)-[:COMPARED_TO]->(BusinessRequest)                 previous_request_id
// (ChangeEvent)-[:CONCERNS]->(Company|Market|Topic)
// (ChangeEvent)-[:EVIDENCED_BY]->(Evidence)
// (Alert)-[:RAISED_FOR]->(ChangeEvent)
// (Alert)-[:ESCALATED_TO]->(BusinessRequest)
// (TimelineEvent)-[:OF]->(Company|Market|Topic)
// (TimelineEvent)-[:EVIDENCED_BY]->(Evidence)
// (User)-[:WROTE]->(Annotation)
// (Annotation)-[:ON]->(Finding|Evidence)


// -----------------------------------------------------------------------------
// 5. SEED: the four workers (fixed reference nodes)
// -----------------------------------------------------------------------------
UNWIND ['internal_intelligence','competitor_intelligence','market_intelligence','customer_trends'] AS t
MERGE (:Worker {type: t});


// -----------------------------------------------------------------------------
// 6. EXAMPLE QUERIES
// -----------------------------------------------------------------------------

// Full provenance chain: decision -> option -> signal -> finding -> evidence -> source
// MATCH path = (d:Decision)-[:CHOSE]->(:DecisionOption)-[:SUPPORTED_BY_SIGNAL]->(:CrossSignal)
//              -[:BASED_ON]->(:Finding)-[:SUPPORTED_BY]->(:Evidence)-[:PUBLISHED_BY]->(:Source)
// WHERE d.id = $decision_id
// RETURN path;

// Verified, non-synthetic pricing findings about one competitor
// MATCH (v:VerificationResult)-[j:JUDGED {status:'verified'}]->(f:Finding {category:'pricing_packaging'})
//       -[:ABOUT]->(c:Company {id:$company_id})
// MATCH (f)-[:SUPPORTED_BY]->(e:Evidence {is_synthetic:false})
// RETURN f.statement, j.evidence_quality, collect(e.source_url);

// How a metric moved over time for a company
// MATCH (o:MetricObservation)-[:MEASURES]->(:Metric {metric_id:$metric_id}),
//       (o)-[:OF]->(:Company {id:$company_id})
// RETURN o.observed_on, o.value, o.unit, o.scope ORDER BY o.observed_on;

// How the comparison with one competitor moved across runs
// MATCH (a:Assessment {workspace_id:$ws})-[:OF]->(:Company {id:$company_id}), (a)-[:ON_DIMENSION]->(t:Topic)
// RETURN t.name, a.observed_at, a.relative_to_target, a.summary ORDER BY t.name, a.observed_at;

// A customer theme across runs
// MATCH (o:ThemeObservation {workspace_id:$ws})-[:OF_THEME]->(:Theme {id:$theme_id})
// RETURN o.observed_at, o.count, o.share, o.sentiment_negative ORDER BY o.observed_at;

// Which tables and endpoints back the internal findings of a run
// MATCH (f:Finding {request_id:$rid})-[:SUPPORTED_BY]->(:Evidence)-[r:RETRIEVED_FROM]->(a:DataAsset)-[:IN_SOURCE]->(d:DataSource)
// RETURN d.name, a.kind, a.name, r.query, collect(f.statement);

// First-party vs third-party support for findings about a competitor
// MATCH (f:Finding)-[:ABOUT]->(c:Company {id:$company_id}), (f)-[:SUPPORTED_BY]->(:Evidence)-[:PUBLISHED_BY]->(s:Source)
// OPTIONAL MATCH (c)-[:OWNS_DOMAIN]->(own:Source) WHERE s.host = own.host OR s.host ENDS WITH '.' + own.host
// RETURN f.statement, s.host, own IS NOT NULL AS first_party;

// Sources whose findings most often fail verification
// MATCH (s:Source)<-[:PUBLISHED_BY]-(:Evidence)<-[:SUPPORTED_BY]-(f:Finding)<-[j:JUDGED]-()
// RETURN s.host, sum(CASE j.status WHEN 'insufficient' THEN 1 ELSE 0 END) AS failed, count(*) AS total
// ORDER BY failed DESC;
