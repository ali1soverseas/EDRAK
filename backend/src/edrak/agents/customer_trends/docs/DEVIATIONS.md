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
