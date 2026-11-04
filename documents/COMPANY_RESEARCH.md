# Company research

The Company Research Agent reads a company's own website and the PDFs it publishes, extracts
facts about the company (leadership, ownership, financials, locations and so on), checks each fact
against the text it came from, and saves the facts as **suggestions**. A person approves, rejects or
edits each one. Nothing is treated as true until it is approved.

It goes beyond the fixed company profile (name, description, products, industry, markets, goals):
the profile stays as it is, and research adds categorized facts, each with the exact quote and the
address (and PDF page) that support it.

## What a fact is

| Field | Meaning |
|---|---|
| `category`, `key` | Where it is filed, for example `leadership` / `ceo`. Keys are lowercase snake_case English. A key the model proposes that matches no canonical key is kept as written, so a person can promote it later. |
| `value`, `value_type` | The fact, as `text`, `number`, `date`, `url` or `list` (a JSON array of strings). |
| `source_tier`, `source_url`, `pdf_page` | Where it was read (see tiers below), and for a PDF the page. |
| `quote` | Text copied from that source that states the fact. Verified in code before the fact is saved. |
| `confidence` | `high`, `medium` or `low`, as judged when the fact was read. |
| `status` | `suggested`, `approved` or `rejected`. |
| `conflict_group` | Shared by facts of one key that disagree. All of them are kept. |
| `edited_by_user` | Set when a person changed the value. |

Values are written in English: amounts with their unit and currency ("EGP 28.4 billion"), dates as
`YYYY-MM-DD` (or `YYYY-MM`, `YYYY`), and person names, places and addresses in Latin script. The quote
keeps the source's language and wording. One amount written in different ways ("EGP20,635,622,860",
"20,635,622,860 EGP") is one value.

Categories: leadership, ownership and funding, financials, customers, partners, locations, team,
pricing, technology, certifications, regulatory, news and events, awards, history, and `other`.
A fact in `other` carries the category name the model proposed. Each category has an English and an
Arabic label (`GET /api/company/fact-categories`).

## Source tiers

| Tier | Source | Default |
|---|---|---|
| `own_site` | Pages of the company's own website (its registered domain and subdomains) | on |
| `own_pdf` | PDFs linked from those pages, including PDFs on a file host or CDN the company's pages point to | on |
| `own_social` | Public bio or about text of the company's own social profiles. Never logs in. Used only when a reader for it is plugged in; the project has none, so the step is skipped with a warning | skipped |
| `third_party` | Top results of a web search (Serper) for categories still empty | **off** |

A third-party fact never replaces one from the company's own site or PDFs. If it disagrees, both are
kept in one conflict group and the person decides. The better source wins a duplicate: the same value
from `own_site` and `own_pdf` is kept once, as `own_site`.

## How a run works

A run is a LangGraph graph over one shared state:

```
seed → crawl → pdfs → extract → gap_check ─┬→ crawl            (up to 2 extra rounds)
                                           └→ own_social → third_party → merge_and_save → finish
```

1. **seed** takes the website from the company's saved links, or the request's `seed_url`, and
   refuses non-public addresses (loopback, private ranges, link-local, credentials in the URL).
2. **crawl** reads the site breadth-first within the page, depth and time limits, honouring
   `robots.txt`, preferring pages whose address or link text names a useful section (about, team,
   investors, locations, history, reports, in English and Arabic). Pages with little static text, or
   that look client-rendered, are opened in a headless browser, which scrolls, clicks "load more" and
   expands tabs and accordions (never buttons that submit, log in or buy). An Arabic copy of a page
   that was read in English is skipped; one Arabic about page is kept.
3. **pdfs** finds PDF links, embedded PDFs, viewer pages that wrap a PDF, and download buttons; ranks
   them (annual and sustainability reports, company profiles and brochures first; terms, privacy and
   forms last; newest year first); downloads the best ones; reads them page by page; and deletes the
   temporary files. A file is accepted only if the response is a PDF by type and by its first bytes.
4. **extract** sends the text to the language model in batches. Each fact must cite its source and
   quote it. Code then checks every quote against its source (Unicode, Arabic spelling and digits,
   case, punctuation and line-end hyphens are normalized); a fact whose quote is not in its source, or
   that cites a source that was not sent, is dropped and counted. Keys are mapped to canonical keys
   through English and Arabic synonyms. Duplicates are merged; facts of one single-valued key with
   different values share a `conflict_group`.
5. **gap_check** lists the categories with no facts. If important ones (a configured list) are empty
   and the round, time and model-call budgets allow, it picks up to a few unread pages of the same
   site whose address or link text matches the empty category, and goes back to **crawl** for one
   more round. At most 2 extra rounds, whatever the setting.
6. **own_social** and **third_party** fill categories still empty, as described under tiers.
7. **merge_and_save** writes all facts found in the run in one transaction, as `suggested`.
8. **finish** records the status, counts, statistics and warnings on the run.

Each step handles its own errors: a failing step adds a warning and the run continues with what it
has. A run ends `failed` only when there is nothing to show: no website, an unreadable homepage, no page
read, facts that could not be saved, or a model that failed on every batch.

### Re-running

Running again does not touch what a person decided. Approved and edited facts are never replaced or
deleted. A value a person rejected is not suggested again. A new value that differs from an approved
fact is added as a suggestion in the same conflict group. A stored suggestion is replaced only by the
same fact from a better source.

## Runs

- A run is a background task. Its row (`research_runs`) is updated after every step with the current
  `stage`, `pages_crawled`, `pdfs_read`, `facts_found`, `warnings` and `stats`, so a screen can poll it.
- One run per company at a time (enforced by the database). Starting another is refused with the
  active run's id.
- A run can be cancelled. A cancelled, failed or timed-out run saves no facts, because facts are
  written only by the last step.
- A run left running by a server restart is marked failed at startup.

## API

All paths are under `/api`, need a signed-in session, and are scoped to the session's workspace.
Errors have the form `{"detail": {"code": "...", "message": "..."}}`.

| Method and path | Purpose |
|---|---|
| `POST /company/research/runs` | Start a run. Body (optional): `{"seed_url": "..."}`. Returns the run, `202`. `422` `no_website` or `invalid_url`; `409` `run_active` (with `run_id`). |
| `GET /company/research/runs` | The company's runs, newest first (`limit`, 1 to 50, default 10). A screen opens on the first one. |
| `GET /company/research/runs/{id}` | Status, `stage`, counts, `stats`, `warnings`, `error`, timestamps. |
| `POST /company/research/runs/{id}/cancel` | Stop a queued or running run. `409` `wrong_state` if it has ended. |
| `GET /company/facts` | Facts grouped by category, with English and Arabic labels. Filters (repeatable): `status`, `category`, `source_tier`. Each category lists `facts` and `conflicts` (facts of one key that disagree, together). |
| `PATCH /company/facts/{id}` | `{"status": "approved" \| "rejected"}`, or `{"value": "...", "value_type": "..."}`. An edit sets `edited_by_user` and approves the fact; the quote and source stay as found. |
| `POST /company/facts/bulk` | `{"ids": [...], "action": "approve" \| "reject"}`, up to 500. Ids that are not the company's are reported in `not_found`. |
| `GET /company/fact-categories` | The category list with English and Arabic labels. |

Review actions are recorded in the audit log. The OpenAPI document (`/openapi.json`) describes all
of the above.

## Configuration

All keys are environment variables (see `.env.example`).

| Key | Default | Meaning |
|---|---|---|
| `COMPANY_RESEARCH_CRAWL_MAX_PAGES` | 40 | Pages read, homepage included |
| `COMPANY_RESEARCH_CRAWL_MAX_DEPTH` | 3 | Links followed from the homepage |
| `COMPANY_RESEARCH_CRAWL_TIME_BUDGET_S` | 180 | Whole crawl |
| `COMPANY_RESEARCH_CRAWL_CONCURRENCY` | 3 | Pages fetched at once |
| `COMPANY_RESEARCH_CRAWL_HOST_DELAY_S` | 1 | Minimum gap between requests to one host |
| `COMPANY_RESEARCH_CRAWL_MAX_BYTES` | 2000000 | Per response |
| `COMPANY_RESEARCH_CRAWL_DYNAMIC` | true | Use the headless browser for sparse or client-rendered pages |
| `COMPANY_RESEARCH_CRAWL_DYNAMIC_MIN_CHARS` | 300 | Static text below this triggers the browser |
| `COMPANY_RESEARCH_CRAWL_MAX_DYNAMIC_PAGES` | 15 | Pages opened in the browser per crawl |
| `COMPANY_RESEARCH_CRAWL_MAX_SCROLLS`, `_SCROLL_PAUSE_MS`, `_MAX_CLICKS`, `_MAX_EXPANDS` | 8, 600, 5, 20 | Browser behaviour per page |
| `COMPANY_RESEARCH_PDF_MAX_PDFS` | 8 | PDFs read per run |
| `COMPANY_RESEARCH_PDF_MAX_MB` | 20 | Per file |
| `COMPANY_RESEARCH_PDF_MAX_PAGES` | 60 | Pages read per file; later pages are left out |
| `COMPANY_RESEARCH_PDF_MAX_ATTEMPTS` | 24 | Downloads tried per run, whatever the outcome (files `robots.txt` forbids are not downloads) |
| `COMPANY_RESEARCH_PDF_CONTEXT_CHARS` | 48000 | Text kept per file |
| `COMPANY_RESEARCH_EXTRACT_BATCH_CHARS` | 24000 | Source text per model call; a document is never split |
| `COMPANY_RESEARCH_EXTRACT_MAX_BATCHES` | 30 | Model calls per run, across all rounds |
| `COMPANY_RESEARCH_EXTRACT_OCR_TOLERANCE` | 0.9 | How closely a quote must match text read by OCR (other text must match exactly) |
| `COMPANY_RESEARCH_RUN_TIME_BUDGET_S` | 900 | No extra round starts after this |
| `COMPANY_RESEARCH_RUN_MAX_S` | 3600 | The run is stopped and failed after this |
| `COMPANY_RESEARCH_IMPORTANT_CATEGORIES` | leadership, ownership_and_funding, financials, locations, history | Categories the gap loop tries to fill |
| `COMPANY_RESEARCH_GAP_MAX_ROUNDS` | 2 | Extra crawl rounds (never more than 2) |
| `COMPANY_RESEARCH_GAP_MAX_TARGETS` | 4 | Pages read per extra round |
| `COMPANY_RESEARCH_THIRD_PARTY` | false | Turn the third-party tier on (needs `SERPER_API_KEY`) |
| `COMPANY_RESEARCH_THIRD_PARTY_MAX_SEARCHES` | 4 | Searches per run |
| `COMPANY_RESEARCH_THIRD_PARTY_RESULTS_PER_SEARCH` | 3 | Top results read per search |
| `COMPANY_RESEARCH_THIRD_PARTY_MAX_PAGES` | 6 | Third-party pages read per run |

The language model, its reasoning level and the one repair retry on an invalid reply are those of the
company profile import (`OLLAMA_API_KEY`, `PROFILE_IMPORT_REASONING`).

## Optional dependencies

- **Playwright and Chromium** (`pip install playwright && playwright install chromium`). Used to read
  pages that load content with JavaScript. Without it the crawl keeps the static text of every page
  and the run carries a warning saying some pages may be incomplete.
- **Tesseract** with Arabic and English language data (`brew install tesseract tesseract-lang`, or
  `apt install tesseract-ocr tesseract-ocr-ara tesseract-ocr-eng`). Used to read scanned PDF pages.
  Without it those pages are skipped and the run names them in a warning; a PDF that is all scans
  gives no facts.

## Limits

- The quote is checked against the source; the **value is not checked against the quote**. A fact
  with a real quote and a wrong value passes verification. The review step is what catches it.
- Only the first 40 pages (by priority) and the 8 best PDFs are read. Large sites and long reports are
  read in part, and the run says so in its warnings.
- Text from multi-column PDF layouts can arrive merged ("Name ANameB"); a fact quoting it is
  verified but reads badly.
- A key describes the company as a whole, but the model sometimes files a division's leader, revenue
  or headcount under the company-wide key (for example several "CEO" values for one group). They show
  up as a conflict group for a person to resolve. Slide and chart text, where a label and its number
  are not next to each other in the source, loses some facts to quote verification.
- Conflict detection compares values as text (amounts excepted). The same fact written in two languages or formats can
  appear as a conflict; a different year or division filed under the same key can too.
- A key that no canonical key matches is stored as proposed; the key table is changed by a person.
- The social tier has no reader yet. The third-party tier needs a Serper key with credit; if a search
  fails (no credit, a bad key, an outage) the step stops at the first failure and the run's warnings give
  the reason.
- A run records at most 100 warnings (each once); files `robots.txt` forbids are counted in one warning.
- The time budget stops new rounds from starting; a step already running finishes (the hard limit
  `COMPANY_RESEARCH_RUN_MAX_S` stops everything).
- The tool follows `robots.txt` and spaces requests per host. A site that blocks automated clients
  (for example behind a bot-protection wall) is reported as unreachable; the tool does not try to get
  around it.
