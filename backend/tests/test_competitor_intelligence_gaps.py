from datetime import datetime, timezone
from io import StringIO

from edrak.agents.competitor_intelligence import gap_nodes


def test_gap_assessment_handles_passage_without_stale_metadata(monkeypatch):
    assessment = gap_nodes.GapAssessment(
        requirements=[
            gap_nodes.RequirementGap(
                requirement_id="R1",
                status="covered",
                score=85,
                best_evidence=["K_R1_1"],
            )
        ]
    )
    prompts = []

    def assess(prompt, _schema):
        prompts.append(prompt)
        return assessment

    monkeypatch.setattr(gap_nodes, "structured_call", assess)
    monkeypatch.setattr(gap_nodes, "open", lambda *_args, **_kwargs: StringIO(), raising=False)
    state = {
        "research_goal": "Compare product availability in Europe.",
        "research_requirements": [
            {
                "requirement_id": "R1",
                "competitor": "Example",
                "research_question": "Which AI accelerator model is available?",
            }
        ],
        "kb_evidence": {
            "R1": {
                "passages": [
                    {
                        "text": "The company offers an AI accelerator model.",
                        "source_url": "https://example.com/products",
                        "source_type": "official",
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
                ],
                "facts": [],
            }
        },
    }

    result = gap_nodes.assess_gaps_node(state)

    assert result["kb_coverage"]["R1"]["status"] == "covered"
    assert len(prompts) == 1
    assert "Compare product availability in Europe." in prompts[0]


def _assess(monkeypatch, judgment):
    monkeypatch.setattr(
        gap_nodes, "structured_call", lambda *_a: gap_nodes.GapAssessment(requirements=judgment)
    )
    state = {
        "research_requirements": [
            {"requirement_id": "R1", "competitor": "Example", "research_question": "Is the accelerator sold?"}
        ],
        "kb_evidence": {
            "R1": {
                "passages": [
                    {"text": "A cloud partnership was announced.", "source_url": "https://example.com/a",
                     "source_type": "official", "fetched_at": datetime.now(timezone.utc).isoformat()},
                    {"text": "Unrelated news.", "source_url": "https://example.com/b",
                     "source_type": "official", "fetched_at": datetime.now(timezone.utc).isoformat()},
                ],
                "facts": [],
            }
        },
    }
    return gap_nodes.assess_gaps_node(state)["kb_coverage"]["R1"]


def test_covered_with_missing_component_is_missing_and_candidates_are_kept(monkeypatch):
    cov = _assess(monkeypatch, [gap_nodes.RequirementGap(
        requirement_id="R1", status="covered", score=90, best_evidence=["K_R1_1"],
        evidence_assessments=[{"evidence_id": "K_R1_1", "relevance": "direct"},
                              {"evidence_id": "K_R1_2", "relevance": "irrelevant"}],
        missing_components=["general availability"])])

    assert cov["status"] == "missing"
    assert "general availability" in cov["missing_information"]
    assert [c["evidence_id"] for c in cov["candidate_evidence"]] == ["K_R1_1", "K_R1_2"]
    assert cov["best_evidence"] == ["K_R1_1"]


def test_covered_needs_direct_evidence(monkeypatch):
    cov = _assess(monkeypatch, [gap_nodes.RequirementGap(
        requirement_id="R1", status="covered", score=95, best_evidence=["K_R1_1"],
        evidence_assessments=[{"evidence_id": "K_R1_1", "relevance": "supporting"}])])

    assert cov["status"] == "missing"


def test_invalid_evidence_id_or_requirement_id_fails_the_batch(monkeypatch):
    bad_evidence = _assess(monkeypatch, [gap_nodes.RequirementGap(
        requirement_id="R1", status="covered", score=90, best_evidence=["K_R9_1"])])
    wrong_requirement = _assess(monkeypatch, [gap_nodes.RequirementGap(requirement_id="R7", status="covered")])
    duplicated = _assess(monkeypatch, [gap_nodes.RequirementGap(requirement_id="R1"),
                                       gap_nodes.RequirementGap(requirement_id="R1")])

    for cov in (bad_evidence, wrong_requirement, duplicated):
        assert cov["status"] == "missing"
        assert len(cov["candidate_evidence"]) == 2


def test_gap_query_planning_accepts_profile_offering_objects(monkeypatch):
    seen_prompts = []
    planned = gap_nodes.GapQueries(
        queries=[
            gap_nodes.GapQuery(
                requirement_id="R1",
                missing_item="accelerator availability",
                query="Example accelerator availability",
            )
        ]
    )

    def fake_structured_call(prompt, _schema):
        seen_prompts.append(prompt)
        return planned

    monkeypatch.setattr(gap_nodes, "structured_call", fake_structured_call)
    state = {
        "research_requirements": [
            {
                "requirement_id": "R1",
                "competitor": "Example",
                "research_question": "When is the accelerator available?",
            }
        ],
        "kb_coverage": {
            "R1": {
                "requirement_id": "R1",
                "competitor": "Example",
                "status": "missing",
                "importance": "high",
                "items": [
                    {
                        "item": "accelerator availability",
                        "status": "missing",
                        "what_is_missing": "accelerator availability",
                    }
                ],
            }
        },
        "kb_profiles": {
            "Example": {
                "official_domains": ["example.com"],
                "offerings": [
                    {"official_name": "Example Accelerator", "doc_url": "https://example.com/accelerator"},
                    {"name": "Example Cloud"},
                ],
            }
        },
    }

    result = gap_nodes.plan_gap_queries_node(state)

    assert "Example Accelerator, Example Cloud" in seen_prompts[0]
    assert result["search_queries"][0]["query"] == "Example accelerator availability"


def test_retrieve_kb_adds_verified_fact_quotes_as_sources(monkeypatch):
    profile = {
        "aspects": {"products_services": {"facts": [{
            "item": "flagship", "statement": "Example sells the Gaudi accelerator", "quote": "Example sells the Gaudi accelerator",
            "source_url": "https://example.com/gaudi", "status": "verified", "relevance": "in_scope"}]}},
    }
    monkeypatch.setattr(gap_nodes, "kb_search", lambda *a, **k: [])
    monkeypatch.setattr(gap_nodes, "kb_profile", lambda key: profile)
    state = {
        "company": "NVIDIA",
        "competitor_info": {"Example": {"kb_key": "example.com"}},
        "research_requirements": [{"requirement_id": "R1", "competitor": "Example",
                                   "research_question": "What does Example sell?"}],
    }

    result = gap_nodes.retrieve_kb_node(state)

    urls = {r["url"]: r for r in result["search_results"]}
    assert "Gaudi accelerator" in urls["https://example.com/gaudi"]["text"]
    assert urls["https://example.com/gaudi"]["from_knowledge_base"]


def test_aspects_for_target_matches_short_company_name():
    profile = {
        "scopes": {"k": {"target": "NVIDIA Corporation"}},
        "aspects_by_scope": {"k": {"technology": {}}},
        "aspects": {"other": {}},
    }

    assert gap_nodes._aspects_for_target(profile, "NVIDIA") == {"technology": {}}
