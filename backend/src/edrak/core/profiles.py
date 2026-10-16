"""Demo fixture: the GitLab pilot profile used by scripts and older tests.

The running app never reads this module. Each workspace's profile lives in the database
(company_profiles) and reaches the workers through BusinessRequest.company_profile.
"""

from edrak.contracts.request import CompanyProfile

GITLAB_COMPANY_PROFILE = CompanyProfile(
    name="GitLab",
    aliases=[
        "GitLab Inc.",
        "GitLab.com",
        "GitLab CI/CD",
        "GitLab DevSecOps",
    ],
    products=[
        "GitLab Free",
        "GitLab Premium",
        "GitLab Ultimate",
        "GitLab Duo",
        "GitLab Duo Agent Platform",
        "GitLab Duo Self-Hosted",
        "GitLab Dedicated",
        "GitLab CI/CD",
        "AI Gateway",
        "GitLab Credits",
        "GitLab Orbit",
    ],
    notes=(
        "GitLab is an enterprise AI-powered DevSecOps platform delivered as a single application. "
        "Its strategic differentiators are a single data model across the software lifecycle, cloud and "
        "AI-model neutrality (multi-model routing via the AI Gateway, including self-hosted models), "
        "privacy and governance controls for AI, and a public, handbook-first operating culture. "
        "In 2026 it repositioned around the 'agentic era' (Act 2)."
    ),
)

COMPANY_PROFILES: dict[str, CompanyProfile] = {
    "gitlab": GITLAB_COMPANY_PROFILE,
}


def get_gitlab_profile() -> CompanyProfile:
    """The demo GitLab profile."""
    return GITLAB_COMPANY_PROFILE.model_copy()
