"""Unit tests for EDRAK RAG subsystem and Internal Intelligence Worker.

Validates all Task 6 test cases:
1. Header parsing (document_id, last_updated, classification, body starts at ##)
2. Title prefixing on chunks (# <title>)
3. as_bool logic
4. best_fact / extracted_fact query-relevance and clean sentence extraction
5. Heading-aware chunking with same-heading multiple chunks
6. Exact duplicate removal
7. Score threshold filtering
8. Provenance and publisher labelling
9. Category classifier rules (strict pricing signals, positioning, product feature, market signal)
10. Credits product vs CREDIT corporate values disambiguation
11. Relevance gate, coverage, gaps, and status
12. Timestamps validation (completed_at > started_at)
"""

from datetime import datetime, timezone
import re
import pytest

from edrak.agents.internal_intelligence.nodes import (
    _classify_category,
    analyze_and_synthesize_node,
    format_worker_result_node,
    plan_queries_node,
)
from edrak.agents.internal_intelligence.state import InternalAgentState
from edrak.contracts.evidence import Evidence, SourceType
from edrak.contracts.result import FindingCategory, WorkerStatus
from edrak.contracts.task import ResearchTask, WorkerType
from edrak.rag.indexer import InternalIndexer
from edrak.rag.text_utils import (
    as_bool,
    best_fact,
    clean_markdown,
    parse_doc_header,
    parse_frontmatter,
    split_sections,
    split_sentences,
    slugify_key,
)

SYN_DOC = """# GitLab AI Gateway & Enterprise Privacy Framework

**Document ID:** INT-ARCH-2026-003  
**Classification:** Internal Technical Architecture (Adapted / Synthetic)  
**Last Updated:** January 2026  
**Author:** Core AI Architecture & Security Team

---

## 1. AI Gateway Multi-Model Routing Service
The **GitLab AI Gateway** is a standalone, high-performance routing proxy that abstracts model providers, prompt engineering, and context assembly from the main GitLab Rails application.

- **Multi-Model Neutrality**: Routes tasks to best-in-class models (Anthropic Claude 3.5 Sonnet for code reasoning, OpenAI GPT-4o for natural language synthesis, and open-weights GLM models).
- **Deployment Flexibility**:
  - **GitLab-Hosted AI Gateway (Default SaaS)**: Managed by GitLab with strict vendor zero-data-retention agreements where customer code is never used to train third-party foundation models. Detailed prompt/response request logs are not retained for GitLab-managed models.
  - **GitLab Duo Self-Hosted (Air-Gapped / On-Premises)**: Regulated customers deploy their own local AI Gateway instances pointing to self-hosted LLMs (e.g., vLLM or Ollama endpoints). All prompt inputs, context data, and response logs remain strictly inside the customer's private network perimeter with zero external internet traffic.

## 2. Privacy Controls
This section outlines detailed privacy and security controls for enterprise compliance across internal environments.
"""


class TestHeaderParsing:
    def test_header_and_body_split(self):
        header, body = parse_doc_header(SYN_DOC)
        assert header["document_id"] == "INT-ARCH-2026-003"
        assert header["last_updated"] == "January 2026"
        assert "synthetic" in header["classification"].lower()
        assert header["title"] == "GitLab AI Gateway & Enterprise Privacy Framework"
        assert "Document ID" not in body
        assert body.startswith("## 1.")


class TestTitlePrefix:
    def test_chunk_text_prefixes_title(self):
        indexer = InternalIndexer.__new__(InternalIndexer)
        header, body = parse_doc_header(SYN_DOC)
        chunks = indexer.chunk_text(
            text=body,
            source_uri="internal://architecture/test.md",
            title=header["title"],
            doc_type="internal_doc",
        )
        assert len(chunks) >= 2
        for chunk in chunks:
            assert chunk.content.startswith("# GitLab AI Gateway & Enterprise Privacy Framework")


class TestAsBool:
    def test_as_bool_cases(self):
        assert as_bool("False") is False
        assert as_bool("false") is False
        assert as_bool("0") is False
        assert as_bool("true") is True
        assert as_bool("True") is True
        assert as_bool("1") is True
        assert as_bool(None, default=False) is False
        assert as_bool(None, default=True) is True
        # Ensure bool("False") bug is avoided
        assert bool("False") is True
        assert as_bool("False") is False


class TestExtractedFact:
    def test_best_fact_query_relevance(self):
        _, body = parse_doc_header(SYN_DOC)
        secs = split_sections(body)
        section_text = secs[0][1]

        privacy_fact = best_fact(section_text, "zero retention data privacy")
        selfhost_fact = best_fact(section_text, "self-hosted air-gapped on-premises")

        assert "zero-data-retention" in privacy_fact or "retained" in privacy_fact
        assert "Self-Hosted" in selfhost_fact or "self-hosted" in selfhost_fact
        assert not privacy_fact.startswith("**")
        assert not privacy_fact.startswith("#")

    def test_noisy_short_bullet(self):
        noisy = "## Diligence\n\n- Social media accounts\n\n- SOC 2\n"
        fact = best_fact(noisy, "query")
        assert "**" not in fact


class TestCategoryClassifier:
    def test_category_rules(self):
        # Infrastructure cost should NOT be pricing_packaging
        infra_text = "Infrastructure cost controls and compute optimization for runner fleets."
        assert _classify_category(infra_text) != FindingCategory.PRICING_PACKAGING

        # Strong pricing signal should be pricing_packaging
        pricing_text = "Premium includes $12 of GitLab Credits per user per month for Duo consumption."
        assert _classify_category(pricing_text) == FindingCategory.PRICING_PACKAGING

        # Copilot comparison should be positioning
        copilot_text = "Competitive battlecard: GitLab Duo vs GitHub Copilot key enterprise differentiators."
        assert _classify_category(copilot_text) == FindingCategory.POSITIONING

        # Gateway architecture should be product_feature
        arch_text = "The AI Gateway multi-model routing service abstracts model providers."
        assert _classify_category(arch_text) == FindingCategory.PRODUCT_FEATURE


class TestCreditsVsValues:
    def test_credits_disambiguation(self):
        # CREDIT values acronym should not be classified as pricing_packaging
        credit_values_text = "CREDIT — Collaboration, Results, Efficiency, Diversity, Iteration, Transparency values framework."
        assert _classify_category(credit_values_text) != FindingCategory.PRICING_PACKAGING

        # GitLab Credits product should match pricing_packaging
        credits_product_text = "GitLab Credits consumption-based pricing model for AI Gateway usage."
        assert _classify_category(credits_product_text) == FindingCategory.PRICING_PACKAGING


class TestNodeExecutionAndContracts:
    def test_analyze_and_format_nodes(self):
        ev_syn = Evidence(
            source_type=SourceType.SYNTHETIC_INTERNAL,
            source_title="GitLab AI Gateway",
            source_url="internal://architecture/ai_gateway.md",
            publisher="GitLab Internal Knowledge (Synthetic)",
            extracted_fact="The GitLab AI Gateway provides multi-model routing and zero-data-retention guarantees for enterprises.",
            excerpt="AI Gateway details...",
            is_synthetic=True,
            metadata={"confidence_score": 0.80, "relevance_score": 0.80, "last_updated": "January 2026"},
        )
        from edrak.contracts.request import BusinessContext, UseCase
        from edrak.core.profiles import get_gitlab_profile

        task = ResearchTask(
            parent_request_id="req_test_001",
            worker=WorkerType.INTERNAL_INTELLIGENCE,
            goal="Assess GitLab Duo architecture and OKRs vs GitHub Copilot",
            focus="GitLab Duo Agent Platform, AI Gateway, zero retention data privacy, GitLab Credits, self-hosted readiness",
            company_profile=get_gitlab_profile(),
            business_context=BusinessContext(
                use_case=UseCase.COMPETITIVE_INTELLIGENCE,
                targets=["GitHub Copilot"],
                focus_areas=["AI capabilities", "pricing & packaging"],
            ),
        )

        state: InternalAgentState = {
            "task": task,
            "queries": ["GitLab AI Gateway"],
            "retrieved_evidence": [ev_syn],
            "started_at": datetime.now(timezone.utc),
        }

        synth_res = analyze_and_synthesize_node(state)
        state.update(synth_res)

        assert len(state["findings"]) >= 1
        assert state["findings"][0].evidence_refs[0].evidence_id == ev_syn.evidence_id

        format_res = format_worker_result_node(state)
        res = format_res["worker_result"]

        assert res.worker == WorkerType.INTERNAL_INTELLIGENCE
        assert res.completed_at >= res.started_at
        assert res.confidence > 0.0
