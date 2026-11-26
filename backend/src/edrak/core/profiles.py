"""Demo fixture: the GitLab pilot profile used by scripts and older tests.

The running app never reads this module. Each workspace's profile lives in the database
(company_profiles) and reaches the workers through BusinessRequest.company_profile.
"""

import json
from pathlib import Path

from edrak.contracts.request import (
    CompanyProfile,
    DatabaseConnectionConfig,
    RESTAPIConfig,
    SocialAccount,
)

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

NVIDIA_COMPANY_PROFILE = CompanyProfile(
    name="NVIDIA",
    aliases=["NVDA", "Nvidia Corporation", "Team Green"],
    website="https://www.nvidia.com",
    social_accounts=[
        SocialAccount(platform="x", url="https://x.com/nvidia"),
        SocialAccount(
            platform="linkedin",
            url="https://www.linkedin.com/company/nvidia",
        ),
        SocialAccount(
            platform="youtube",
            url="https://www.youtube.com/user/nvidia",
        ),
    ],
    products=[
        "Blackwell GPUs (B200, GB200 NVL72)",
        "Hopper GPUs (H100, H200)",
        "CUDA & cuDNN Software Platform",
        "NVIDIA AI Enterprise & NIM Microservices",
        "Quantum-2 InfiniBand & Spectrum-X Ethernet Networking",
        "GeForce RTX Graphics",
        "Omniverse Platform",
    ],
    notes=(
        "NVIDIA is the pioneer of GPU-accelerated computing and full-stack AI platforms, "
        "designing industry-leading GPUs, networking interconnects (InfiniBand), CUDA "
        "software stacks, and enterprise AI software suites for data centers, gaming, "
        "robotics, and edge computing."
    ),
    db_config=DatabaseConnectionConfig(
        db_id="nvidia_enterprise_db",
        connection_uri="sqlite:///mock_services/nvidia/nvidia_enterprise.db",
        allowed_tables=["gpu_products", "enterprise_deals", "software_arr"],
        description="NVIDIA Enterprise relational database (hardware pricing, deals, software ARR).",
    ),
    api_config=RESTAPIConfig(
        api_id="nvidia_ops_api",
        base_url="http://127.0.0.1:8088",
        description="NVIDIA operational cluster telemetry and deal desk microservices API.",
    ),
)

COMPANY_PROFILES.update(
    {
        "nvidia": NVIDIA_COMPANY_PROFILE,
        "nvda": NVIDIA_COMPANY_PROFILE,
        "nvidia corporation": NVIDIA_COMPANY_PROFILE,
        "team green": NVIDIA_COMPANY_PROFILE,
    }
)


def get_gitlab_profile() -> CompanyProfile:
    """The demo GitLab profile."""
    return GITLAB_COMPANY_PROFILE.model_copy()


def get_nvidia_profile() -> CompanyProfile:
    """The NVIDIA profile used for NVIDIA-focused runs."""
    return NVIDIA_COMPANY_PROFILE.model_copy(deep=True)


def get_company_profile(name: str) -> CompanyProfile:
    """Resolve a known company profile by canonical name or alias."""
    key = name.strip().lower()
    return COMPANY_PROFILES.get(key, CompanyProfile(name=name.strip()))


def get_detailed_profile(name: str) -> dict:
    """Load the matching detailed JSON profile, when one exists."""
    profile_dir = Path(__file__).resolve().parents[4] / "data" / "profiles"
    aliases = {
        "nvidia": "nvidia_profile.json",
        "nvda": "nvidia_profile.json",
        "nvidia corporation": "nvidia_profile.json",
        "team green": "nvidia_profile.json",
        "gitlab": "gitlab_profile.json",
    }
    filename = aliases.get(name.strip().lower())
    if not filename:
        return {}
    path = profile_dir / filename
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def get_detailed_gitlab_profile() -> dict:
    return get_detailed_profile("gitlab")
