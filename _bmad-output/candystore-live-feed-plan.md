# Candystore Live Project Feed — Design & Implementation Plan

**Date:** 2026-09-04
**Board:** Plane `CANDYS` (`82e56896-e7fd-466b-826c-1019441c64ca`) — epics **E8–E14**, tickets **CANDYS-32…68**
**Supersedes nothing.** Extends `candystore-deep-dive-plan.md` (epics E1–E7, CANDYS-2…31).

## The ask

> "This app in its current form is not as useful as it could be." — real-time updates; an intuitive way
> to filter the deluge; copy the idiomatic patterns from Datadog and Splunk **but stop at the query bar**
> — *"I don't need a query language. I don't want to type in queries. Here's how I want to differentiate
> from the big dogs: since I don't have to cater to a million people, I can cater to myself."*

The target user story, verbatim: pick a project from the registry → see its events → a mode-switchable
graphic timeline above the feed with color-coded dots per event provenance (CLI hooks, n8n workflows,
plain webhooks, agents, subagents) → switch the mode to events-per-minute, live → click the **PM** lens
and see far less → read each event as a formatted envelope with a truncated body → uncheck **show tool
calls** and get a colored dot with a count in their place.

## How this was produced

A four-dimension research workflow (5 agents, 814k tokens): Datadog/Splunk mechanics; a peer survey of
Grafana Loki, Honeycomb, Sentry, DevTools, k9s, Jaeger, GitHub Actions, LangSmith/Langfuse and the
Railway/Vercel/Fly log streams; a streaming-architecture decision record measured against the live
stack; and a codebase/data gap analysis. Then one arbitration pass that resolved conflicts and cut
anything not serving the story. Every number below was measured against the running system on
2026-09-04, not estimated.

## Three findings that reshaped the plan

1. **Tool calls are 95.9% of the trail** (agent.tool.requested 94,205 + completed 65,611 of 166,723 in
   7d). Collapsing them is not a nice-to-have — it takes james-brennan's 7-day feed from **85,128 rows
   to ~2,165**. Nothing else in this plan changes the experience as much.

2. **"Select a project from the registry" does not work today, and is also 20 seconds slow.**
   `PROJECT_EXPR` falls back to `basename(working_directory)`, and `data->>'project'` is set on 0.17% of
   rows — so the measured "project" list is polluted with worktrees (`feat-cartesia-agents`,
   `jimb-169-prod-incident-20260901`), subdirectories (`.agents`, `dist`, `web`, `mirror`) and
   `james-brennan.git`. The picker costs 11.59 s and the click costs another 8.45 s. There are only
   **718 distinct working directories across all 886k rows**, so this is a 718-row lookup problem.

3. **The stale "68% of events render generic" claim is refuted — and the gap inverted.** Measured today
   it is **0.98%** (1,644 of 166,723), so CANDYS-25 is effectively satisfied *by volume*. But those
   1,644 rows are almost exactly the events this feature is for: `repo.task.*` 711,
   `repo.decision.recorded` 229, `system.process.exited` 227. **100% of the PM lens renders as a bare
   type string** while `ticket_key`, `phase` and `decision` sit unread. Volume-weighted coverage was the
   wrong metric; lens-weighted coverage is the one that matters.

Two live defects were found incidentally and are ticketed: `GET /events/stream` already returns an empty
reply with no status line (the `/events/` prefix branch swallows it), and `list_events`' unbounded
`COUNT(*)` is a **4,057 ms / 25.8 GB-of-buffers** query on every page load.

---
## 1. The design, in one page

**One screen, `/projects/:slug`.** Three horizontal bands, no sidebar.

**Band 1 — Control bar (sticky, 40px).** Project picker (a `<select>` of the 24 registry slugs, each with a 24h event count) · lens chips · `☐ Show tool calls` · time window presets (`15m 1h 24h 7d`) · the existing `q` box · `⏸/▶` live toggle · a permanently visible state strip (k9s): `live · tools:off · lens:pm · 24h`. Every mode that changes what you see is legible without opening anything. Right edge: `⧉ copy as API call` — the honest escape hatch (Datadog raw mode, Splunk "Open in Splunk platform"), ours is `GET /events?...`.

**Band 2 — Timeline strip (120px).** Stacked bar histogram, ~40 buckets sized so the window yields ~40 bars, bucket width printed in the legend (`1 min per column` — Splunk's exact wording). **Stacked by provenance class**, not by event type (94% of events are one type; stacking by type is one solid block). Two modes on one selector: `Count` (stacked bars) and `Rate /min` (line, `count × 60 / bucket_seconds` — no new query, just a divisor), plus a numeric `142 events/min` readout in the header, which is worth more than the chart while live. `Linear | Log` toggle, because `Bash` flattens everything else. Drag to brush → floating `Filter to selection (14:02–14:09 · 3,880 events)` button (two-step commit, Log Observer). Click one bar → window = that bucket. Clicking a legend swatch filters to that class.

**Band 3 — Feed.** Newest at top. Fixed-height 28px rows, `line-clamp-1`, monospace numerals. Row anatomy:

```
│ 14:02:11  ● claude-code   Bash          git status --porcelain            412ms
│ 14:02:09  ● claude-code   Edit          …/candystore/candystore/query.py    18ms
├ 14:01:44  ✗ codex-cli     Bash          pytest tests/ -v                  8.1s
│ 14:01:02  ● 33god-pm      JIMB-254 · In Review ← In Progress
```

- 3px left stripe + glyph carries **outcome only** (`✗` red, `●` neutral). Never a row tint at this density.
- The dot's hue carries **provenance class**. Two encodings, two channels, no shared hue — Datadog documents exactly what breaks when you color by two dimensions at once (red stops meaning bad).
- Middle-truncate paths, not end-truncate: the discriminating token in this corpus is at the end.
- Click a row → **right side panel** (never an inline accordion; an accordion reintroduces variable heights and kills the virtualizer). Panel = full JSON + field/value table, and a 5-item per-field menu: `Copy` · `Add to filter` · `Exclude from filter` · `View in session` · `View in context`. Selecting a row auto-pauses the stream (Datadog).

**The collapsed tool-call affordance, precisely.** With `Show tool calls` off, every consecutive run of tool events between two orchestrator events becomes **one 28px fold row**:

```
▸ ●47   Bash×39 · Read×5 · Edit×3            14:02:11 → 14:03:40   1m 42s
  ↑     ↑                                                          ↑
  dot   top-3 tools with counts                                    span
  count (bold, tabular-nums) — the primary scan token
```

`▸` expands **in place** (Jaeger/DevTools/Honeycomb — this is a rendering of a range, not a durable entity, so it never navigates), capped at 200 members with a `show all 1,034` continuation. `⇧`-click expands every fold in view. **An error terminates a run and gets its own row** — a fold can therefore never contain a failure, which is why the fold needs no error badge. Runs of ≤2 render as plain rows (a chevron over one item is noise). Measured effect: james-brennan 7d goes 85,128 → 2,165 rows.

**New-arrival handling.** Pinned to top → prepend, with a 120ms opacity fade and a background that decays over 1.5s (Loki's new-arrival flash). Animate opacity and background only — never height or margin. Scrolled away (`scrollTop > 8`) → **auto-pause**, freeze the list, and show one sticky pill `↑ 214 new · 2 errors` that resumes and jumps to top on click. One bar, one action (Discord's documented failure is two bars whose actions cross). On resume, fire one bounded backfill for the pause gap and prepend; if the gap exceeds the page, insert a persistent divider `⋯ 3,880 events between 14:02 and 14:09 — click to load`. The tail fold row grows in place (`47 → 48`), which converts ~95% of arrivals from layout-changing inserts into free text updates — the highest-leverage consequence of the collapse.

**What we're copying.** Datadog: pause-on-row-select, pause button placement, the 6-color chart cap, side-panel two-zone layout, `View in context`. Splunk Log Observer: bucket-width legend, two-step brush commit, per-value `=`/`!=` buttons, 1000-row pagination. Grafana Drilldown: Include/Exclude on every rendered value, removable filter pills, "no query language" as the product thesis. k9s: permanent toggle-state strip, 50ms/100ms flush timer. Sentry: grouping is a *setting* with a visible off switch. LangSmith/Langfuse: the turn is the collapsible unit; ship a flat log view alongside any tree.

**What we're doing differently (the bet).** No query language, and no query bar as the source of truth. Datadog needs syntax highlighting, paren-balance errors, a raw-mode escape, and a natural-language `Ask` button because the query string is authoritative — that is the itemized cost of the choice we are refusing. Our state lives in **the URL as a structured object** (`?project=&lens=&class=&tools=0&from=&to=&q=`), which buys shareability, back/forward, saved views, and agent-consumable deep links without a grammar. AND/OR is declared by fiat: **multi-select within a facet = OR, across facets = AND.** OR across *different* facets is the one thing that genuinely needs a language; we don't build it and we say so in the empty state. Also different: we hoist errors out of every fold, which no surveyed product does.

**Provenance color assignment (7, none red, none green — those are reserved for outcome):**

| class | color | 7d rows |
|---|---|---|
| `agent` (CLI orchestrator) | blue `#3b82f6` | 150,328 |
| `subagent` | violet `#8b5cf6` | 15,451 |
| `pm_agent` (Hermes PM/momo) | teal `#14b8a6` | 218 |
| `ticket_webhook` (Plane via n8n) | pink `#ec4899` | 713 |
| `n8n_workflow` | yellow `#eab308` | 32 |
| `service` (tiller, deathwatch, skills) | slate `#64748b` | 681 |
| `other` | gray `#9ca3af` | 3 |

Chart capped at 6 series + `other` in the same gray, colors repeat past six (Datadog's literal rule). Never hue alone: `✗` with red, `●` with neutral.

**State model.** One `useReducer` at the page level in a context. State: `{filters, rows, folds, buckets, tail:{paused, buffered, evicted}, status}`. Eight actions: `FILTERS_SET`, `PAGE_LOADED`, `TAIL_APPENDED`, `TAIL_TRIMMED`, `FOLD_EXPANDED`, `MODE_SET`, `ERROR`, `RESET`. Two `useMemo` selectors derive rendered rows and strip buckets from the same `rows` array, so the strip and the feed can never disagree.

**One rule that ties the whole thing together: a count in this UI is always the true count of matching events, even when the rows are not shown.** Only rows are ever sampled; counts never are.

---

## 2. Decisions, with the rejected alternative

| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| Transport | **SSE on the existing HTTP/1.0 `ThreadingHTTPServer`** | WebSocket; long-poll; interval-poll `/events` | No stdlib WebSocket, and traffic is one-directional. HTTP/1.0 with no `Content-Length` *is* the SSE framing model; `wbufsize=0` installs `_SocketWriter` whose `write` is `sendall`, so no flush calls and no chunked encoding. 500 concurrent streams = 14.9 MB RSS, 29.8 kB/conn. Interval-polling `/events` is disqualified by measurement: 4,057 ms / 25.8 GB of buffers per call from the unbounded `COUNT(*)`. |
| Backend event source | **In-process fan-out: `deque(maxlen=500)` + `threading.Event`** | Postgres `LISTEN/NOTIFY`; keyset polling as the steady state | One process is verified (`/proc/1/cmdline` = `python -m candystore.main`, 1 thread). Fan-out costs 10 µs against an existing 6 ms POST. `deque.append` on a full deque evicts and returns — it cannot raise, which is the whole poison-storm safety argument; `queue.Queue.put_nowait` is **forbidden** because `queue.Full` is exactly that bug. NOTIFY's hard payload ceiling is 7,999 bytes and 4.66% of envelopes exceed it (max 120,800), so it degrades to an id-only signal plus a re-SELECT per event — all the DB cost, none of the simplicity, to solve a multi-process problem this stack forbids. |
| Tail cursor column | **`(received_at_micros, id)`, no `seq` column** | `time`; a new `BIGSERIAL` | A `time`-ordered cursor silently drops 1 event in 238 (701 inversions in 7d, worst backstep 212.9 s). `received_at` has microsecond resolution and **zero ties in 167,051 rows**. A sequence would not fix the commit-order race either; both are solved by a 2s lookback + id-dedupe, so the sequence buys nothing. Requires `CREATE INDEX idx_events_received_at` (~19 MB): 105.92 ms / 1,326 MB of buffers → 0.169 ms / 52 buffers. |
| Filter pushdown | **Server-side, per subscriber; two stages** | Ship everything, filter in the browser | Measured 33.3× byte reduction with tools hidden, 259.8× vs full envelopes. Decisive non-byte reasons: the browser cannot resolve project (the registry is server-side), and a 2,000-row client ring filled with 96.8% tool calls holds **64** usable rows. Stage 1 (scope) drops and doesn't count; Stage 2 (`tools=0`) drops rows but **still counts** into the tick frame — that's what makes the fold count exact. |
| Project resolution | **`project_dir_map` table (718 rows) synced from `pjangler project list --json`, longest-prefix** | `PROJECT_EXPR` basename; naive `LIKE prefix%`; per-row correlated subquery | Basename yields `dist`, `web`, `.agents`, `james-brennan.git`, `feat-cartesia-agents`. `data->>'project'` is on 0.17% of rows. Longest-prefix is mandatory, not an optimization: `/home/delorenj/code/33GOD` is a prefix of four other registry paths. Match must be `wd = path OR wd LIKE path||'/%'` — `intelliforia` is a string prefix of `intelliforia-mobile`. The correlated version measured 141 s over 886k rows; a 718-row lookup table is the right shape and a new project is a row insert, not a migration. |
| The 8.4-second first click | **Default time window + cheap-count path. No table rewrite in P0.** | `work_dir GENERATED STORED` + `text_pattern_ops` index (P1) | **[measured today]** Project-filtered feed, 24h bound, `LIMIT 200`, no `COUNT`: **166.57 ms** (29.34 ms with tools excluded), versus 8,450 ms today. That is a 50× win with zero DDL, and it takes the ~2.5-minute `ACCESS EXCLUSIVE` rewrite off the critical path entirely. |
| Provenance classifier | **Full classifier (incl. `data->'payload' ? 'agent_id'`) as a SQL expression constant in `query.py`** | Inline-columns-only lane; `summarize.py`; a generated column | The inline-only lane cannot distinguish agent from subagent, which is half the user's dot list. **[measured today]** full classifier `GROUP BY class, minute`: **18.05 ms over 60 min** (the default window), 212.23 ms over 24 h; inline-only 37.08 ms/24h; `actor->>'type'` alone 16.56 ms/24h. So the detoast costs ~175 ms at the 24h mode switch and is free at the default. It must live in SQL because the story *clicks a dot to filter* and the strip needs `GROUP BY class` — `summarize.py` runs per-row in Python after the rows return and can never serve a `WHERE`. |
| Frontend state | **`useReducer` + one context** | TanStack Store; TanStack Query | The hard part is server-state lifecycle (cancellation, keep-previous, append-not-refetch, three derived views off one row array), which a store solves none of. One reducer, 8 actions, 2 selectors, one component subtree, no cross-route sharing. Retire the `AGENTS.md` "PLANNED: TanStack Store" line. |
| Virtualization | **`@tanstack/react-virtual`, fixed `estimateSize: () => 28`, landing with the live tail** | react-window; none at all; variable-height rows | Uniform height means zero measurement, which is what makes a 2,000-row ring scroll at 60fps. Not needed at 200-row pages, so it ships with the tail rather than blocking the first click. Re-key the virtualizer on density change instead of measuring per row. |
| Aggregate strip data | **One SSE channel: `ev` / `tick` / `ctrl` frames, seeded by one bootstrap query** | A second SSE channel; a polled `/summary` on a timer | A second channel burns one of the browser's 6 per-origin connections and, worse, two independently filtered channels can disagree about the same window — then the strip is decoration. Ticks count all scope-matching events including presentation-hidden tool calls. Recharts 2.15.4 is already installed, so no new chart lib. |
| Feed order | **Newest at top, auto-pause on scroll-away** | Append newest-at-bottom with stick-to-bottom | Peer-tools is right that prepending while scrolled down is a trap — so we make that state unreachable: prepend only while `scrollTop <= 8`, otherwise pause and buffer. Newest-at-top matches Datadog, Splunk, the existing app, and historical pagination; flipping it would rewrite the whole UI to solve a problem auto-pause already solves. |

---


---

## 3. The work — epics E8–E14 on CANDYS

Full acceptance criteria, evidence and dependencies live on each ticket. This is the map.

| Ticket | Title | Prio | State |
|--------|-------|------|-------|
| **CANDYS-32** | **Project Truth — resolve every event to a PJangler registry project** | **epic** | Backlog |
| CANDYS-33 | Bound /events by time and stop the unbounded COUNT(*) | P0 | Todo |
| CANDYS-34 | Add project_dir_map and a `mise run project:sync` task | P0 | Todo |
| CANDYS-35 | Add GET /projects backed by the PJangler registry | P0 | Todo |
| CANDYS-36 | Filter ?project= by registry slug through the map | P0 | Todo |
| CANDYS-37 | Delete the second project implementation | P0 | Backlog |
| CANDYS-38 | Spike: time the work_dir generated column on a restored copy | P2 | Backlog |
| CANDYS-39 | Spike: decide how /tmp scratch clones attribute to a project | P2 | Backlog |
| **CANDYS-40** | **The Readable Row — a flat render contract for the feed** | **epic** | Backlog |
| CANDYS-41 | Add a flat, always-present summary.row contract | P0 | Todo |
| CANDYS-42 | Drop `data` from the /events list payload | P0 | Backlog |
| CANDYS-43 | Add repo.task, repo.decision, repo.board and process.exited summarizers | P0 | Todo |
| CANDYS-44 | Spike: find every external consumer of /events `data` | P2 | Backlog |
| **CANDYS-45** | **Lenses, Provenance and Collapse — filtering without a query language** | **epic** | Backlog |
| CANDYS-46 | Add PROVENANCE_EXPR and a ?class= filter | P0 | Todo |
| CANDYS-47 | Add named lenses (?lens=) | P0 | Todo |
| CANDYS-48 | Collapse consecutive tool runs server-side into counted fold rows | P0 | Todo |
| CANDYS-49 | Add GET /summary/timeline for the header strip | P0 | Todo |
| CANDYS-50 | Spike: is payload.agent_type='default' the orchestrator or a subagent? | P1 | Backlog |
| **CANDYS-51** | **The Screen — /projects/:slug on a reducer** | **epic** | Backlog |
| CANDYS-52 | Rebuild EventList as /projects/:slug on a reducer | P0 | Todo |
| CANDYS-53 | Render the timeline strip: count mode, log toggle, brush | P1 | Backlog |
| **CANDYS-54** | **Live — SSE tail with a poison-proof fan-out** | **epic** | Backlog |
| CANDYS-55 | Add idx_events_received_at | P1 | Backlog |
| CANDYS-56 | Add GET /events/stream (SSE) with a poison-proof fan-out | P1 | Backlog |
| CANDYS-57 | Wire the client tail: pause, pill, ring buffer, virtualization | P1 | Backlog |
| CANDYS-58 | Spike: does Traefik deliver SSE unbuffered through the OIDC middleware? | P1 | Backlog |
| **CANDYS-59** | **Agent Surface — parity for the primary consumer** | **epic** | Backlog |
| CANDYS-60 | Add a candystore query CLI | P2 | Backlog |
| CANDYS-61 | Publish a Candystore query skill | P2 | Backlog |
| CANDYS-62 | Expose /metrics from stats.snapshot() plus a live subscriber count | P2 | Backlog |
| **CANDYS-63** | **Producer Contracts — fix provenance at the source** | **epic** | Backlog |
| CANDYS-64 | Stamp data.hook on claude-code events and canonicalize the spelling | P3 | Backlog |
| CANDYS-65 | Fix codex-cli parent_invocation_id (121/121 are self-referential) | P3 | Backlog |
| CANDYS-66 | Set correlationid on hermes-agent events (1.8% coverage today) | P3 | Backlog |
| CANDYS-67 | Fix the n8n Plane node truncating repo/slug to 4 characters | P3 | Backlog |
| CANDYS-68 | Register the 10 unregistered repos in pjangler | P3 | Backlog |
### The critical path

Eleven stories make the verbatim user story executable, in this order:

**CANDYS-33 → 34 → 35 → 36 → 41 → 43 → 46 → 47 → 48 → 49 → 52**

CANDYS-33 goes first because it is a ~30-line change that turns the 8.45-second first click into
166 ms and de-risks everything downstream. CANDYS-42 and CANDYS-37 can land anywhere in the
sequence. At the end of CANDYS-52 the user can pick a registry project, see its feed, see a
class-stacked timeline above it, click the PM lens, read formatted rows, and uncheck tool calls to
get counted dots.

**Explicitly follow-on:** CANDYS-53 (brush, log scale, rate mode), all of E12 (live tail — until
then the strip's real-time mode is a 5-second refetch of `/summary/timeline` against a bounded
window, which is honest and cheap), all of E13, all of E14.

### Relationship to the E1–E7 backlog

- **Closes** the untickted E7-2 *"Materialize + index the project column"* and the
  `derived-project-expr-fullscan` finding — by fixing correctness first (CANDYS-34/36) and only then
  measuring whether the index is still worth an `ACCESS EXCLUSIVE` lock (CANDYS-38). It probably is not:
  CANDYS-33 already gets the click to 166 ms.
- **Complements, does not duplicate, CANDYS-25** (summarizers). That ticket covered the high-volume
  `agent.tool.*`/`agent.session.*` families and is measurably done. CANDYS-43 covers the low-volume,
  high-signal `repo.*` families it never reached.
- **Depends on CANDYS-24** (`schema_migrations` ledger) being *absent*: `init_schema()` still replays
  every migration on every boot, so migrations 005 and 006 must self-guard exactly the way
  `004_search_caps.sql:38-57` does. Landing CANDYS-24 first would make that unnecessary.
- **Feeds CANDYS-19/20/21/22** (session-linkage fidelity): CANDYS-66 is the producer-side half. Fixing
  the historical backfill without fixing hermes-agent's 1.8% `correlationid` coverage reopens the same
  gap tomorrow.
- **Constrains CANDYS-16** (authenticate the query API): `/events/stream` and `/metrics` are data
  routes and belong inside the auth boundary, not in the `/healthz`-style exempt list.
- **Retires** the `AGENTS.md` *"PLANNED: TanStack Store"* line rather than honoring it — see the state
  decision in §2.

---

## Progress

**2026-09-04 — Epic E8 (Project Truth) shipped and verified live.** CANDYS-33/34/35/36/37 → Done.
The story's first click works: pick a registry project, see its events, and every row reports the
same project the filter used.

| | before | after |
|---|---|---|
| project picker (`/projects`) | 11.59 s, garbage buckets | **0.29 s**, exactly the 24 registry slugs |
| first click (`?project=…&limit=200`) | 8.38 s | **0.032 s** |
| unbounded `/events` page | 8.45 s (4.02 s of unread `COUNT(*)`) | **0.32 s** |
| query plan | `Parallel Seq Scan` | `Bitmap Index Scan` on `idx_events_time` |
| `project` agreement across endpoints | 38 of 50 rows disagreed | **0 of 60** |

Three corrections to this plan, found while building:

1. **`data.project` cannot be rung L1.** Measured over the whole table, three of its five distinct
   values are entire JSON objects serialized as text (`{"name": "James Brennan", "slug": …}`) and its
   highest-volume plain string is `wax` (2,803 rows), which is not a registered project. Trusting it
   would render a JSON blob as a project name. It is honored only when it names a real registry slug.
2. **There were three project derivations, not two.** `query.py` had its own `_project_from_data`
   feeding `session_summary`, and the tool summarizers carried no `project` key at all. All
   consolidated; `project` is now nullable data everywhere and `unassigned` is a label used only in
   prose and on chart axes.
3. **CANDYS-38's premise is weaker than written.** CANDYS-33's time bound got the click to 166 ms
   with zero DDL, and CANDYS-36 then took it to 32 ms. The `work_dir` generated column would buy
   single-digit milliseconds for a ~2.5-minute `ACCESS EXCLUSIVE` lock. Recommend closing it
   won't-do unless a measurement asks.

Resolution rate: 90.44% of placed 7-day events reach a registry project (135,718 `repo-path` +
11,482 `repo-subpath` + 1,745 `sibling-worktree` of 164,697). The residual is dominated by repos
never registered — CANDYS-68 moves it toward ~1.5%.

---

## 4. Spikes

| Spike | Question | Unblocks | Timebox |
|---|---|---|---|
| **CANDYS-50 — `payload.agent_type='default'`** | Are those 160 rows (9 distinct `agent_id`) the orchestrator or an unnamed subagent? Read one codex rollout at `payload.transcript_path`. | Whether 160 rows are dotted violet or blue in CANDYS-46. | 1 h |
| **CANDYS-58 — Traefik SSE buffering** | Does `candystore.delo.sh` deliver SSE frames unbuffered through the OIDC middleware? `curl -N` through the authenticated route, assert inter-frame arrival < 1 s. Go's `ReverseProxy` should auto-flush on `text/event-stream`, unverified because the route is auth-gated. | Whether CANDYS-56 needs `responseForwarding.flushInterval: -1`. Config-only either way. | 30 min |
| **CANDYS-38 — `work_dir` generated-column wall time** | Actual `ALTER TABLE ... ADD COLUMN ... GENERATED STORED` duration on the current 4,874 MB table. Time it on a `pg_dump`-restored copy; `004_search_caps.sql` documents ~2 min at 871k rows and that is an extrapolation. | Whether the P1 index optimization is worth scheduling at all, given CANDYS-33 already got the click to 166 ms. Do **not** run this on the live table before the spike. | 2 h |
| **CANDYS-39 — `/tmp` scratch-clone attribution** | Should `/tmp/hermes-board-cranker-50` resolve via its `git_branch` ticket prefix, or should the board-cranker harness set `data.project` at spawn? ~4,900 rows/wk. | The last rung of the CANDYS-34 ladder. It is a decision, not a measurement — the harness knows the answer; the trail is guessing. | 30 min |
| **CANDYS-44 — external `/events` consumers** | Does anything outside this repo read `event.data` from the list response? `grep` the fleet/skills repos for `/events?` callers. | Whether CANDYS-42 needs `?include=data` as a default rather than an opt-in. | 30 min |

---

## 5. Explicitly out of scope

- **A query language.** No DQL, SPL, LogQL, or `@arrayAttribute[i]:value`. Railway is the cautionary tale: it started as an opinionated log stream and grew a dialect nobody knows. The existing `q` trigram box stays as a convenience; clicking is the primary interaction. Our escape hatch is `⧉ copy as API call` and the CLI, which is what lets us refuse a language honestly.
- **OR across different facets.** Genuinely needs a grammar, genuinely rare. Say so in the empty state. Two saved views (i.e. two URLs) is the substitute.
- **Facet auto-discovery.** Splunk's 20%-presence rule exists because its fields are unknown; ours are known and hand-picked. Skip it.
- **Auth, RBAC, HA, multi-tenancy, rate limits, audit of the audit trail.** Loopback-only, one user, OIDC at the edge. Explicitly unwelcome.
- **FastAPI / asyncio / SQLAlchemy / Alembic / a WebSocket library / a state library.** The stack is stdlib-only by design and the measurements say it is not the bottleneck: 500 SSE streams cost 14.9 MB RSS and fan-out sustains 100k events/s against a 152/s measured peak.
- **Postgres `LISTEN/NOTIFY` and a trigger on `events`.** 7,999-byte payload ceiling vs 4.66% of envelopes over it (max 120,800 B). Rejected on measurement.
- **A `seq BIGINT` column.** `received_at` has microsecond resolution and zero ties in 167,051 rows; a sequence would not fix the commit-order race either.
- **The `work_dir` generated column in P0.** **[measured today]** the time-bound + cheap-count fix gets the click to 166 ms with zero DDL. Do not take a 2.5-minute `ACCESS EXCLUSIVE` lock on the ingest path to buy a further 130 ms until a measurement asks.
- **Datadog's 3–4 nested group-by dimensions, Log Patterns / Pattern Inspector / the Smaller↔Larger slider, Transactions with custom start/end conditions, Watchdog outlier ranking, duration heatmaps, `Save as event type`, alerts.** One stacking dimension capped at 6+other. `arguments`-shape clustering (Fold C) is the only one with a real ceiling and it is a week of work for a question the two cheap folds mostly answer — revisit after CANDYS-52 ships.
- **A rate sparkline as a separate widget.** It is the count histogram with information removed; the numeric `N events/min` readout in the header does the job.
- **User-configurable columns, drag-to-reorder, column resize.** Fields are known; ship the absolute/relative timestamp toggle and three density settings instead.
- **A subagent tree for claude-code (66% of volume).** Not reconstructible from the data that exists. The codex-only two-level tree (15,415 rows) ships in CANDYS-60 **labeled as codex-only**. Do not paper over it.
- **A "plain webhook (not via n8n)" dot.** Zero instances in the corpus; it would render empty forever. Collapse the user's two webhook classes into one `ticket_webhook` dot, split by `data.provider` if a second level is ever wanted.
- **A separate "CLI hooks" dot.** Undeliverable for 66% of volume until CANDYS-64 lands: claude-code emits no `data.hook` at all. Until then `agent` and `cli-hook` are the same set and shipping both would be a lie.
- **TanStack Store.** Retire the `AGENTS.md` "PLANNED" line rather than honor it.
- **Prepend-vs-append cleverness, smart-scroll heuristics, scroll-position compensation.** Two states only: pinned (prepend) or paused (buffer + pill). There is no third case.