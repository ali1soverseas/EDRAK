"""Print what each provider costs per 100 items for every capability, in routing order.

Run from backend/: uv run python ../scripts/customer_trends/print_costs.py
Prices come from config/providers.yaml (Apify per-item prices, SocialCrawl credits per page and
the USD price of a credit), so changing a pack price there updates this table.
"""

from edrak.agents.customer_trends.providers.config import load_providers_config
from edrak.agents.customer_trends.providers.costs import cost_table


def main() -> None:
    config = load_providers_config()
    credit = config.providers["socialcrawl"].options.get("usd_per_credit")
    print(f"USD per 100 usable items, in routing order (SocialCrawl at {credit} USD per credit)\n")
    for capability, row in cost_table(config).items():
        cells = []
        for name, cost in row:
            if cost is None:
                cells.append(f"{name}: not offered")
            else:
                cells.append(f"{name}: {cost:.4f}" if cost else f"{name}: free")
        print(f"{capability:<26} " + "  >  ".join(cells))


if __name__ == "__main__":
    main()
