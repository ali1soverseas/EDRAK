# EDRAK frontend

React 19, TypeScript and Vite. Built from the design handoff in `edrak-ui-handoff/` (kept out of git, see
`.gitignore`). The look is that design: `src/styles/edrak.css` and `src/styles/fonts.css` are copied from it
**unchanged**.

## Run it

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

Open the app in a Chromium browser. The brief's blur and parallax use CSS scroll timelines, which Chrome, Edge and
other Chromium browsers support. Other browsers get the same layout without that motion.

There is no backend for runs, plans or briefs yet, so the app runs against an in-browser mock (see below).

**Demo sign-in:** `layla@nileledger.example` / `edrak-demo`. Or create an account: any email, a password of 8+
characters. Data is kept in `localStorage`. In the dev console, `edrakMock.reset()` restores the seed data.

| Command | What it does |
|---|---|
| `npm run dev` | Dev server with hot reload |
| `npm run build` | Type-check, then production build to `dist/` |
| `npm test` | Unit tests (Vitest) |
| `npm run gen:types` | Regenerate `src/types/contracts.ts` from the schema file (see Contract types) |

## Screens and routes

| Route | Screen | File |
|---|---|---|
| `/sign-in` | Sign in and create account | `pages/SignIn.tsx` |
| `/company` | Company setup | `pages/Company.tsx` |
| `/` | Analyses | `pages/Dashboard.tsx` |
| `/analyses/new` | New analysis (`?use_case=`, `?draft=`) | `pages/NewAnalysis.tsx`, `components/RequestForm.tsx` |
| `/analyses/:id/plan` | Plan review | `pages/PlanReview.tsx` |
| `/analyses/:id/run` | Live run, polled every 1.5 s | `pages/LiveRun.tsx`, `components/RunStatus.tsx` |
| `/briefs/:id` | Brief and source drawer | `pages/Report.tsx`, `components/ReportView.tsx`, `components/brief/*`, `components/EvidencePanel.tsx` |

English and Arabic. Arabic sets `dir="rtl"` on `<html>` and the layout mirrors. Strings are in `src/i18n/en.ts` and
`ar.ts`; the compiler fails if Arabic is missing a key. Text that comes from the backend (findings, sources, supervisor
log lines) is shown as written.

## How the code is laid out

```
src/
  styles/        edrak.css + fonts.css (the design, unchanged), then app.css and one file per screen
  types/
    contracts.ts   GENERATED from the Pydantic contracts. Never edit by hand.
    app.ts         types the contracts do not cover: session, company, analysis rows, run progress, brief
  services/
    api.ts         EdrakApi: the only thing screens call
    mock/          the mock behind it: seed data, planner, run simulation, content packs
  lib/            pure logic (brief derivation, form to request, hooks)
  i18n/           provider, en.ts, ar.ts
  components/     shell, ui primitives, brief cards, source drawer
  pages/          one per route
```

### Rules from the handoff, kept

- **Do not edit `edrak.css`.** Anything it does not cover goes in `app.css` or a screen file, using its tokens.
  Do not add one-off colours; add a token.
- **Motion is CSS only**, using the keyframes in `edrak.css`: `Hub` (sign in, live run), `.grow-bar`, `.caret`, the
  `.bscroll` / `.bc-*` scroll timelines. The brief container must stay the scroll element, and `.bstatic` must not
  be used. `prefers-reduced-motion` is honoured by `edrak.css`.
- **Evidence numbers** (`E14`) open the source drawer. **Reliability marks** come from the verdict's
  `evidence_quality`: high is three, medium two, low one.
- **Not built, on purpose** (not in the MVP): roles, monitors, alerts, watchlist, decision log, Ask Edrak, exports,
  Google or Microsoft sign-in, notifications, search, settings.
- The brief's **comparison matrix** and **evidence chain** have no source in the contracts, so card 3 shows the evidence
  table on its own. The cross-signal synthesis strip on card 2 appears only when the brief carries a `synthesis`.

## Contract types

Frontend types for contract objects are generated, not written:

```bash
# from the repository root
.venv/bin/python scripts/export_contract_schemas.py   # Pydantic models -> frontend/src/types/contracts.schema.json
cd frontend && npm run gen:types                       # schema -> frontend/src/types/contracts.ts
```

Run both after any change under `backend/src/edrak/contracts/`, and commit the two generated files.

## The mock, and replacing it

`services/api.ts` defines `EdrakApi` and exports `api`. Today `api` is `services/mock/mockApi.ts`. To go live, write
an `HttpApi` that implements `EdrakApi` and export it from `api.ts`. No screen changes.
`edrak-ui-handoff/docs/mvp_screens_to_tables.md` lists what each screen reads and writes, which is the endpoint list.

What the mock stands in for, and what is demo behaviour rather than product behaviour:

- **Sign in** checks a hashed password stored in `localStorage`. It is not authentication.
- **The Supervisor's plan** is a template, one task per allowed worker.
- **A run** is a pure function of time (`services/mock/simulate.ts`): every worker's progress, the supervisor log, the
  re-plan and the finish come from how long ago the plan was approved. Market-entry runs deliberately lose the
  Market worker so the failed-worker and partial-evidence states can be seen.
- **Briefs** are built from content packs (`services/mock/packs.ts`) as real contract objects (`WorkerResult`,
  `VerificationResult`, `OrchestrationResult`), then passed through the same `buildBrief` the real data will use.
- All names, sources, quotes and numbers are invented.
