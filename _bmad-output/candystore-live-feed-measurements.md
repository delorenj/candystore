# Candystore live-feed measurements — 2026-09-04, against the live stack

All figures measured, not estimated. Reproduce with:
`docker exec candystore-postgres psql -U candystore -d candystore -At -c "<SQL>"`

## Volume and shape
- 885,243 rows, 4,874 MB total relation size.
- Last 7 days: 166,723 events.
- Tool calls dominate: agent.tool.requested 94,205 + agent.tool.completed 65,611 = 159,816 = **95.9%**.
- Everything a human would call "signal" is the remaining 4.1%: agent.invocation.completed 2,390,
  conversation.turn.started 1,070, agent.session.ended 830, agent.invocation.started 567,
  agent.session.started 422, repo.task.updated 365, repo.decision.recorded 229,
  system.process.exited 227, repo.task.appended 224, repo.task.created 122.

## Ingest rate (30d, per minute, on received_at)
p50 **11**/min · p95 **76**/min · p99 **181**/min · **max 663**/min (~11/s) · 18,805 minutes with events.
=> a live feed does NOT need sampling at these rates, but does need a bounded client buffer.

## Tail cursor: `time` is NOT safe, `received_at` is (and is unindexed)
- received_at - time lag: p50 0.01s · p99 0.06s · **max 213.22s** · 0 future-dated rows.
- **537 of 167,060 rows (0.32%) arrive out of chronological order** — i.e. ordered by received_at,
  their `time` is earlier than the previous row's `time`.
  => a tail cursor on `time` silently skips those rows. Must use received_at (or a new monotonic seq).
- `received_at` has **no index**. Existing indexes are all on `time`, type, domain, producer, service,
  correlationid, actor->>'cli', search_text. See `SELECT indexname FROM pg_indexes WHERE tablename='events'`.

## Process model (in-process fan-out is viable)
- Dockerfile CMD is `python -m candystore.main` — ONE process, ONE ThreadingHTTPServer. No gunicorn,
  no multi-worker. So an in-memory pub/sub between ingest and SSE subscribers is sound.
- `protocol_version` is **never set** in candystore/ → BaseHTTPRequestHandler defaults to **HTTP/1.0**,
  which has no chunked transfer-encoding and no keep-alive. SSE needs this changed to "HTTP/1.1".
- The app joins the external `proxy` network; remote access is candystore.delo.sh behind Traefik +
  google-auth OIDC. Postgres and daprd stay off proxy.

## Provenance is derivable from `source`, but the scheme is not normalized
urn:33god:agent:claude-code 109,390 · urn:33god:agent:codex-cli 32,415 · urn:33god:agent:hermes 21,061
urn:33god:agent:antigravity-cli 1,334 · urn:33god:integration:copilot-cli 892
urn:33god:integration:n8n:plane-webhook 713 · urn:33god:service:traefik-deathwatch 227
urn:33god:service:tiller 114 · urn:33god:skill:activity-report 9 · urn:33god:cli:smoketest 1
hermes://agent/33god-pm 120 · hermes://agent/james-brennan-momo 46 · hermes://agent/james-brennan-pm 36
NON-CONFORMING: //big-chungus/wax 271 · urn:pr-crusher:runner 46 · urn:t 2 · hermes-agent:jacksnaps-pm 1
Supporting columns: actor.type = agent_cli 165,314 / ticket_provider 713 / service 690 / operator 8 / '' 18.

## data.hook is present on 55,702 rows in 4+ casing conventions at once
PreToolUse 29,678 · pre_tool_call 10,395 · post_tool_call 10,036 · PostToolUse 2,345 · preToolUse 449
PreInvocation 446 · PostInvocation 444 · postToolUse 411 · pre_llm_call 319 · on_session_end 275
SubagentStop 223 · SessionStart 213 · UserPromptSubmit 168 · SubagentStart 121 · Stop 111
subagent_stop 36 · userPromptSubmitted 9 · agentStop 8 · sessionEnd 7 · sessionStart 7

## Subagent linkage is present but partly self-referential
- data.parent_invocation_id on 567 of 2,958 agent.invocation.* rows (7d).
- Newest SubagentStart row has parent_invocation_id == invocation_id == thread_id
  ("2cca9fd1-f246-42aa-a6f2-9225c7501312") — self-referential, so no tree can be built from it as-is.
- Other candidate linkage in the payload: turn_id, thread_id, session_id, agent_id,
  data.payload.agent_type (e.g. "code-reviewer"), and the SubagentStart/SubagentStop hook names.

## "Project" is broken for the registry-selection story
- query.py PROJECT_EXPR falls back to basename(working_directory); data->>'project' is set on only
  **281 of 166,723** rows (0.17%).
- Measured top "projects" (7d) mixes real projects with worktrees and subdirectories:
  james-brennan 63,497 · unknown 23,887 · pjangler 21,833 · intelliforia 13,866 · vinyl 11,078 ·
  **feat-cartesia-agents 3,533 (worktree)** · bloodbank 3,501 ·
  **jimb-169-prod-incident-20260901 2,899 (worktree)** · slowburns 2,388 · relay 1,956 ·
  **james-brennan-jimb169 1,719 (worktree)** · **.agents 1,126** · hermes-board-cranker-50 997 ·
  candystore 952 · **dist 523** · **repo 97** · **web 84** · **mirror 81** · james-brennan.git 267
- The canonical registry is pjangler. `pjangler project list --json` returns, per project:
  name, slug, repo_path, description, status, ticket_provider{identifier, board_id, workspace}.
  24 projects. Note slug != basename in at least one case: slug "bb" -> /home/delorenj/code/33GOD/bloodbank.

## Summarizer coverage — the stale plan doc's "68% generic" is REFUTED, but the gap inverted
- SUMMARIZERS covers 12 canonical types (candystore/summarize.py), plus the tool.tool_call.* rename map.
- Measured 7d: **165,370 covered / 1,644 generic = 0.98% generic**. CANDYS-25 is effectively satisfied
  for volume.
- BUT the 1,644 generic rows are precisely the user story's "PM lens":
  repo.task.updated 365 · repo.decision.recorded 229 · system.process.exited 227 ·
  repo.task.appended 224 · repo.task.created 122 · repo.maintenance.* 46 · repo.board.created 11 ·
  repo.intake.triaged 9 · project.activity.recorded 9 · reporting.report.completed 7 ·
  plus audio.* ~250 and finance.* ~120.
  => 100% of the PM/decision lens renders as a bare type string. Volume-weighted coverage was the
  wrong metric; lens-weighted coverage is the one that matters for this feature.

## Ticket board reality
- PLANE_33GOD_API_KEY is **write-scoped**: LIST /issues, /states, /labels all return total_count 0
  even though CANDYS-2..31 exist. The provider adapter
  (agents/hermes/pm/.scripts/providers/plane.sh list_issues) returns `[]`.
- Other Plane keys in op://DeLoSecrets/Plane (Cack/Grolf/Lenoon/Rar/Tongy/apiKey/AutomaticAI API Token)
  all return 403 "You do not have permission" on the 33god workspace — they belong to other workspaces.
- Existing board: 7 epics CANDYS-2..8, 23 stories CANDYS-9..31. Next sequence starts at CANDYS-32.
- No CANDYS rows in the trail's ticket_key data (boards seen: JIMB 409, PJAN 55, 33GOD 39, PROJ 5,
  SKIPM 4, SLOWBURNS 4, SKRILL 1, AAI 1) => the CANDYS board is not wired to the n8n Plane webhook.
