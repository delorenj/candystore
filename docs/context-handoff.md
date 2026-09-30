# Resume project work across agent CLIs

`candystore context latest` reads a project's recent Bloodbank history and
returns a compact handoff across Claude, Codex, Gemini, Kimi, Hermes, Copilot,
OpenCode, and other recorded CLI sessions. You can use it directly or receive
the same output automatically at agent startup.

```bash
cd /home/delorenj/code/33GOD/candystore
mise run cli:install
candystore context latest
candystore context latest --project bloodbank --since 7d
candystore context latest --project candystore --sessions 1 --json
```

The defaults are three sessions with activity in the past 30 days, ordered by
their last recorded activity. Project detection uses the main Git checkout
and CandyStore's PJangler registry projection; subdirectories and worktrees
resolve to the project. Submodules keep their own identity. `--project` accepts
a registry slug or alias, so `bloodbank` resolves to `bb`.

`--since` accepts a duration such as `24h`, `7d`, or `4w`, or an ISO date/time.
`--sessions` accepts 1–10. The CLI automatically excludes the current session
when its ID is exported by the agent. Hooks supply their native session ID
explicitly; for manual use, `--exclude-session <id>` does the same. Both UUID
correlations and historical non-UUID session IDs are supported.

## What the handoff contains

Each session includes its first sampled request and latest follow-up, outcome excerpts, files
reported modified, decisions, explicit unfinished work, past tool failures,
and recorded next steps. Every fact carries an event ID and timestamp in JSON;
the text includes event IDs so an agent can inspect the original evidence with
`GET /events/<event-id>/raw`.

These are historical statements, not a fresh inspection of the checkout. A
session ending does not mean its goal was completed. Requested edits are not
presented as successful changes, and past errors are not automatically called
unresolved blockers. When no next step is recorded, the command suggests
checking the current diff and checks before resuming the latest request.

No model call is needed: the handoff extracts recorded evidence deterministically.
Consequently, a CLI that never captured a prompt or final response cannot supply
one; the output identifies those gaps. Sampling is bounded to the last 60
narrative events and 120 tool events per session and reports when a sample is
truncated. Project decisions with independent correlation IDs are listed
separately. The text briefing is capped at 8,000 characters and keeps room for
each selected session; `--json` retains the additional sampled facts. Turn-end
answers missing project fields inherit the selected session's project, while
events explicitly attributed to another project stay excluded.
Credential-shaped text is redacted from the generated briefing;
the underlying events are not rewritten.

## Startup integration

The shared Bloodbank hook registry owns a `candystore-context` handler at
`services/hook-hub/handlers.toml`. Existing native startup hooks call `bb-hook`,
which runs the CLI and formats its text for the receiving agent. Claude, Codex,
Gemini, Kimi, Copilot, Hermes, and OpenCode use their session-start boundary;
Antigravity uses its first `PreInvocation`. OpenClaw has no deployed startup
binding and is outside the installed coverage.

The handler has a bounded deadline. Missing CLI installation, unavailable
CandyStore, an unknown project, or a timeout records a failed/skipped handler
and leaves the agent able to start. Set `CANDYSTORE_CONTEXT=0` to skip it, or
disable `candystore-context` through the existing project hook controls.

```bash
CANDYSTORE_CONTEXT=0 codex
CANDYSTORE_URL=http://127.0.0.1:8683 candystore context latest --project candystore
```

`CANDYSTORE_URL` and `--base-url` choose the API; the default is the local
loopback endpoint. `--timeout` controls the manual HTTP deadline (default 5
seconds). Retrieval errors print to stderr and return exit status 1 without
emitting a partial handoff. `candystore` with no arguments, `candystore serve`,
and `python -m candystore.main` continue to start the HTTP server.

## API and deployment

`GET /context/latest` accepts `project` or absolute `cwd`, plus `since`,
`sessions`, and `exclude_session`. It returns schema version 1 with `project`,
`since`, `as_of`, `excluded_session`, `sessions`, and independent `decisions`.
Unknown projects and invalid bounds return 400; a database failure returns 503.

```bash
mise run migrate:context
mise run deploy:app
mise run cli:install
mise run context:latest
```

`migrate:context` creates the additive lookup indexes concurrently while the
existing service keeps ingesting. `deploy:app` recreates only the actual
`33god-platform` CandyStore app; it does not start the legacy standalone stack
alongside it. The shared hook hub reloads the handler registry automatically.

Run database tests only against a disposable database using
`CANDYSTORE_TEST_DATABASE_URL`; the suite truncates its test audit tables.
