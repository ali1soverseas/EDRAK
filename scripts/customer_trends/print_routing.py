"""Print the provider routing of `config/providers.yaml` as a Markdown table.

The table in docs/ARCHITECTURE.md is this output; a test keeps the two equal. Run from `backend/`:

    uv run python ../scripts/customer_trends/print_routing.py
"""

from edrak.agents.customer_trends.providers.config import ProvidersConfig, load_providers_config

HEADER = "| Capability | 1st | 2nd | 3rd |\n|---|---|---|---|"


def _switched_off(config: ProvidersConfig, provider: str, capability: str) -> bool:
    actors = config.providers[provider].options.get("actors", {})
    spec = actors.get(capability)
    return provider == "apify" and (
        spec is None or not spec.get("actor") or spec.get("enabled") is False
    )


def routing_rows(config: ProvidersConfig) -> list[str]:
    """One table row per capability, in alphabetical order; a provider switched off for a
    capability is marked `(off)`."""
    rows = []
    for capability in sorted(config.routing):
        cells = [
            f"{name} (off)" if _switched_off(config, name, capability) else name
            for name in config.routing[capability]
        ]
        padded = [*cells, *[""] * (3 - len(cells))]
        rows.append(f"| `{capability}` | " + " | ".join(padded[:3]) + " |")
    return rows


def routing_table(config: ProvidersConfig | None = None) -> str:
    return "\n".join([HEADER, *routing_rows(config or load_providers_config())])


if __name__ == "__main__":
    print(routing_table())
