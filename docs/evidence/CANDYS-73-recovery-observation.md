# CANDYS-73 — independent recovery observation

Observed by candystore-pm on November 22–23, 2026. **AC6 remains unproven; do not close.** This is a coverage finding, not proof that the startup fix regressed.

## What was executed

The retained rollout recipe was attempted unchanged and reached its five-minute execution bound while walking the unfiltered observation feed. A bounded read-only PostgreSQL query then used the existing type/time and JSONB indexes, repeatable-read transactions, statement and lock timeouts. No events, application code, service configuration or credentials were changed.

The original **[2026-11-17 19:31, 2026-11-18 19:31) UTC** window still has immutable observations: 1,092 invocations after deduplicating 1,214 revisions; 534 succeeded, 509 skipped/not_first_turn, 17 skipped/no_project, 2 skipped/no_history, 1 skipped/context_disabled, 28 failed/handler_exit_nonzero and 1 timed_out/timeout. Its receipt journal has expired. Neither that empty journal nor those unclassified failures is a passing observation.

A newer complete settled window, **[2026-11-21 23:00, 2026-11-22 23:00) UTC**, has both receipts and immutable observations. Publication was checked through 23:10 UTC:

| Outcome | Invocations |
| --- | ---: |
| succeeded / handler_completed | 180 |
| skipped / not_first_turn | 209 |
| skipped / no_project | 40 |
| skipped / no_history | 3 |
| failed / handler_exit_nonzero | 14 |
| **Total** | **446** |

496 observation revisions deduplicate to the same 446 receipts. There are **zero missing, differing or observation-only invocations**. A separate read-only journal aggregate agrees. Both expected skip reasons were genuinely observed and published.

## Why AC6 is still held

Nine failures are proven genuine retrieval errors: each originating invocation joins through its published source event to an exact excluded native-session identity in PostgreSQL statement-timeout diagnostics. Application errors corroborate them. Genuine failures are allowed by AC6 and were not erased.

Five failures remain **unknown**, not proven genuine or false. Their requests were for a registered project, but the shared handler discards child CLI stderr. Receipt details, bounded hub/application/PostgreSQL logs, native journals and a bounded crank-log identity search did not recover their cause. Four-second duration and a successful replay now do not prove a historical timeout. The outer handler's zero exit is also not child success.

The five unresolved requests began November 22 at 03:00:05.950, 03:15:04.106, 03:20:04.906, 03:25:04.981 and 03:35:04.997 UTC. Private evidence retains the exact identity joins; no session content is included here. A second already-elapsed day also reconciled, but retained two unclassified failures and cannot justify closure either.

Installed CLI and actual symlinked handler were independently exercised for Claude SessionStart and Hermes first-turn adapters: home and an unregistered directory produce no_project; a registered live project with both history arrays empty produces no_history; populated history still injects context; an actual closed-loopback transport failure remains failed without injection. These are current boundary checks, not substitutes for historical classification.

## Bounded next step and ownership

The historical collection above is complete. **Missing diagnostics, not elapsed time or publication, block the verdict.** [BB-64](https://plane.delo.sh/33god/browse/BB-64/) is the routed prerequisite: Bloodbank owns the shared handler and receipt boundary. Request a narrow, privacy-safe failure classification tied to invocation identity, with actual child exit distinct from wrapper exit; do not publish arbitrary stderr, prompts, context or credentials. First check whether the five exact original diagnostics survive elsewhere. If not, retrospective classification is impossible for this window: after independently reviewed diagnostic coverage exists, collect one complete bounded post-change day and compare receipts with immutable observations before another verdict.

No schedule was installed: another scheduled read cannot recover discarded diagnostics. Any later time-based follow-up must have a committed Candybar-owned manifest and proven execution; no independent clock is authorized. The required read-only Candybar live policy lint passed its repository checks but returned machine **unknown** for Earshot's externally pinned PM execution without an enabled Candybar owner; that unrelated ownership finding was not changed.

Private immutable snapshots, manifests and bounded logs are retained under `agents/hermes/pm/runtime/evidence/CANDYS-73-recovery-20261122/` with restricted permissions; public evidence contains only aggregates. Penny's collection cache and live watermark were not touched.

Basis: **Dogfood the Platform** and **Build LEGO, Not Statues** — use the existing receipt/audit paths, preserve genuine errors, and fix the shared evidence seam rather than add a second monitor.
