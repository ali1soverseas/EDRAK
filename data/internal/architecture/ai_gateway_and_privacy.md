# GitLab AI Gateway & Enterprise Privacy Framework

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
