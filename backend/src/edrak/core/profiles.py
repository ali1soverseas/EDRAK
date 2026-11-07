"""Fixtures for company profiles used by scripts, evals, and tests.

The running app reads company profiles from the database (company_profiles)
and reaches workers through BusinessRequest.company_profile.
"""

from edrak.contracts.request import CompanyProfile

NVIDIA_COMPANY_PROFILE = CompanyProfile(
    name="NVIDIA Corporation",
    aliases=[
        "NVIDIA",
        "Nvidia",
        "NVDA",
        "NVIDIA Corp",
    ],
    products=[
        "H100",
        "H200",
        "B200",
        "GB200 NVL72",
        "A100",
        "DGX H200",
        "HGX H200",
        "GB300",
        "CUDA",
        "cuDNN",
        "TensorRT",
        "TensorRT-LLM",
        "NCCL",
        "NVIDIA AI Enterprise",
        "NIM",
        "NeMo",
        "Triton Inference Server",
        "DGX Cloud",
        "Spectrum-X",
        "BlueField-3",
    ],
    notes=(
        "NVIDIA Corporation designs and manufactures accelerated computing platforms, high-performance "
        "GPUs (Hopper H100/H200, Blackwell B200/GB200 NVL72), networking solutions (Spectrum-X Ethernet, "
        "Quantum-2 InfiniBand), and an enterprise software stack (CUDA, cuDNN, TensorRT-LLM, Triton, "
        "NVIDIA AI Enterprise, NIM microservices, NeMo). In 2026, NVIDIA powers hyperscale AI training, "
        "frontier model inference, and sovereign AI factory infrastructure worldwide."
    ),
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
        "GitLab is an enterprise AI-powered DevSecOps platform delivered as a single application."
    ),
)

COMPANY_PROFILES: dict[str, CompanyProfile] = {
    "nvidia": NVIDIA_COMPANY_PROFILE,
    "gitlab": GITLAB_COMPANY_PROFILE,
    "default": NVIDIA_COMPANY_PROFILE,
}


def get_nvidia_profile() -> CompanyProfile:
    """The demo NVIDIA profile."""
    return NVIDIA_COMPANY_PROFILE.model_copy()


def get_gitlab_profile() -> CompanyProfile:
    """Backward compatibility alias: returns NVIDIA profile for current workspace evaluation."""
    return NVIDIA_COMPANY_PROFILE.model_copy()


def get_demo_profile() -> CompanyProfile:
    """Returns the primary demo profile (NVIDIA)."""
    return NVIDIA_COMPANY_PROFILE.model_copy()
