"""Predefined company profiles for EDRAK pilot companies and benchmarks."""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from edrak.contracts.request import CompanyProfile
from edrak.core.config import settings

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
    """Returns the standardized CompanyProfile contract model for the GitLab pilot."""
    return GITLAB_COMPANY_PROFILE.model_copy()


def get_detailed_gitlab_profile() -> Dict[str, Any]:
    """Loads the comprehensive JSON profile containing full strategic, financial, and product data."""
    active_path = settings.BASE_DIR / "data" / "profiles" / "active_profile.json"
    if active_path.exists():
        try:
            with open(active_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    profile_path = settings.BASE_DIR / "data" / "profiles" / "gitlab_profile.json"
    if profile_path.exists():
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return GITLAB_COMPANY_PROFILE.model_dump()


def get_company_profile(company_name: str) -> CompanyProfile:
    """Retrieves a pre-configured CompanyProfile by name or constructs a default profile."""
    # Check active_profile.json first
    active_path = settings.BASE_DIR / "data" / "profiles" / "active_profile.json"
    if active_path.exists():
        try:
            with open(active_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not company_name or data.get("name", "").strip().lower() == company_name.strip().lower():
                    return CompanyProfile(
                        name=data.get("name", company_name.strip() or "Company"),
                        aliases=data.get("aliases", []),
                        products=data.get("products", data.get("offerings", [])),
                        notes=data.get("notes", data.get("description")),
                    )
        except Exception:
            pass
    key = company_name.strip().lower()
    if key in COMPANY_PROFILES:
        return COMPANY_PROFILES[key].model_copy()

    # Attempt to load from data/profiles/<key>_profile.json
    profile_path = settings.BASE_DIR / "data" / "profiles" / f"{key}_profile.json"
    if profile_path.exists():
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return CompanyProfile(
                    name=data.get("name", company_name.strip()),
                    aliases=data.get("aliases", []),
                    products=data.get("products", []),
                    notes=data.get("notes"),
                )
        except Exception:
            pass

    return CompanyProfile(
        name=company_name.strip(),
        aliases=[],
        products=[],
        notes=f"Auto-generated profile for {company_name.strip()}",
    )
