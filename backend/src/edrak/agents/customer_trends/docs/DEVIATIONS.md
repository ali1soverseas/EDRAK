# Deviations from SPEC

SPEC.md was aligned with the repository conventions (AGENTS.md, CONTRIBUTING.md) before implementation started. Open points that still need a team decision are listed in SPEC section 15.

## Batch 3

- SPEC 8.2 routes `social_comments` for x, tiktok, instagram and facebook to apify, socialcrawl and serper. Serper can only run a `site:` search and cannot fetch the comments of one post, so `social_comments:*` is routed to apify and socialcrawl only. Reddit comments follow the same rule.

## Batch 4

- SPEC 8.3 lists `apify/facebook-posts-scraper` for Facebook. That actor reads the posts of one page URL and cannot search by keyword, and the one keyword-search actor tried (`powerai/facebook-post-search-scraper`) returned no items for two queries while charging its start fee. `social_search:facebook` is therefore switched off for Apify (`enabled: false` in providers.yaml, mapper kept) and served by SocialCrawl, then Serper.
- SPEC 8.3 lists a single `apify/instagram-scraper` for Instagram. Its keyword `search` returns hashtag pages, not posts, so Instagram search reads the posts of the hashtag made from the query. Comments use `apify/instagram-comment-scraper`.
- SPEC 8.3 lists `streamers/youtube-scraper` and `clockworks/tiktok-scraper` for comments too. Comments use the vendors' dedicated comment actors (`streamers/youtube-comments-scraper`, `clockworks/tiktok-comments-scraper`, `apify/facebook-comments-scraper`), which are cheaper per item.

## Batch 5

- SPEC section 9 gives `social_comments(platform, post_url, + RequestBase)`. A `sort` argument (`top` or `recent`, default `top`) was added, because the providers can order comments and the most-liked ones are the most useful default.

## Cost-based routing

- SPEC 8.2 lists Apify first for social search and comments, and routes `search_interest` to Apify and the Google Trends API only. Measured costs and speed (see ARCHITECTURE.md, Routing and cost) put SocialCrawl first for TikTok, Instagram, Reddit and Facebook search and for every comments capability except YouTube, and YouTube's API, then SocialCrawl, then Apify for YouTube. `search_interest` gains SocialCrawl as a second provider between Apify and the stub. X search keeps Apify first.

## Batch 6

- SPEC 6.6 defines `ThemeAggregate` without quotes. It now has `representative_quotes` (default empty), so quotes are saved with the aggregates and reach the final result. The shared contract is not touched.
- SPEC 6.7 and the batch ask for a loose numeric match of 1 percent or 0.5 absolute. A flat 0.5 lets 0.3 match 0.7 for shares and rates, so the absolute part is the rounding of the number as written: 0.5 for a whole number (12 matches 12.4 but not 12.6), 0.05 for one decimal, 0.005 for two. The 1 percent relative match is unchanged. Whole numbers behave as specified.
- SPEC section 9 gives `compute_metrics(metric, batch_ids, params={})`. `batch_ids` is optional here (default: every batch of the run) and `params` takes a `filters` entry for the evidence metrics.

## Batch 7

- SPEC section 10 lists `gaps: list[str]` in the state. Gaps are records with a severity and a suggested action, held as JSON dicts, because the replan edge and the replan prompt need both. The result's `gaps` field is still a list of strings.
- SPEC section 10 names `plan: QueryPlan | None` and `brief: TaskBrief` in the state. They are stored as JSON dicts (see ASSUMPTIONS, Batch 7). Three fields were added: `branch_errors`, `metric_ids`, `warnings`.
- SPEC section 10 says "bounded tool-using sub-agents" without naming a builder. `langchain.agents.create_agent` is used and `langchain` was added to `pyproject.toml`, because `langgraph.prebuilt.create_react_agent` is deprecated in the installed version.
- SPEC section 11 and the batch require `run_worker` and the shared adapters. They are pending the shared contracts in this branch (ASSUMPTIONS, Batch 7).
- `tool_called` events gained a `batch_id` field, and `run_task` / `stream_task` a `settings` argument; neither is in SPEC section 11.

