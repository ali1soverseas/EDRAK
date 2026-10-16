# GitLab Duo Agent Platform — Product Architecture & Capabilities

**Document ID:** INT-PROD-2026-001  
**Classification:** Internal Strategic Documentation (Adapted / Synthetic)  
**Last Updated:** February 2026  
**Author:** Product Management — AI & DevSecOps Workflows

---

## 1. Executive Summary & General Availability
The GitLab Duo Agent Platform achieved General Availability (GA) on January 15, 2026. Transitioning from early assistive chatbots ("Duo Core"), the Agent Platform provides autonomous, multi-step agentic workflows that act across the entire software development lifecycle—spanning issue breakdown, code generation, automated review, security vulnerability remediation, and CI/CD build failure diagnosis.

## 2. Core Architecture & Workflow Execution
1. **Intelligent Orchestration Engine**: Operates natively within GitLab's unified data model. Unlike editor-only plugins, agents possess full context of epics, issues, merge requests, CI pipelines, and audit logs.
2. **Specialized Lifecycle Agents**:
   - **Planning Agent**: Translates business requirements and epics into technical issue hierarchies and acceptance criteria.
   - **Code & Refactor Agent**: Implements multi-file changes across repositories with automated test generation.
   - **Reviewer Agent**: Conducts semantic code reviews, style enforcement, and architectural compliance checks.
   - **Vulnerability Resolution Agent**: Analyzes SAST/DAST findings and automatically proposes validated patch merge requests.
   - **CI/CD Root Cause Analysis Agent**: Triages broken build logs and suggests pipeline configuration fixes.
3. **Human-in-the-Loop Governance**: Every autonomous agent action requires policy-defined approval gateways before merging code or deploying to production environments.
