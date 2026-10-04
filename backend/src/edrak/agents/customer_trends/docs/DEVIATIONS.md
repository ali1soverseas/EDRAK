# Deviations from SPEC

SPEC.md was aligned with the repository conventions (AGENTS.md, CONTRIBUTING.md) before implementation started. Open points that still need a team decision are listed in SPEC section 15.

## Batch 3

- SPEC 8.2 routes `social_comments` for x, tiktok, instagram and facebook to apify, socialcrawl and serper. Serper can only run a `site:` search and cannot fetch the comments of one post, so `social_comments:*` is routed to apify and socialcrawl only. Reddit comments follow the same rule.
