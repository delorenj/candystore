# CANDYS-73 — startup context rollout evidence

UTC activation: **2026-11-17T19:30:24Z** (Bloodbank `main` push completed; CandyStore `main` had already been pushed and its CLI installed). The hook resolves `/home/delorenj/.agents/hooks/hub/concerns.py` to `/home/delorenj/code/33GOD/bloodbank/services/hook-hub/concerns.py`; the installed `/home/delorenj/.local/bin/candystore` resolves to `/home/delorenj/.local/share/uv/tools/candystore/bin/candystore` (`uv tool install --editable` from CandyStore main). No app/API/container deployment was required: this change is the host CLI and symlinked handler only.

| Repository | Pushed `origin/main` | Reviewed change |
| --- | --- | --- |
| CandyStore | `70ea3044bb2d67e4ace3568b8aeb2f0a450aac0a` | `57915cf7c6024d80c36c714d32d383008a471671` |
| Bloodbank | `958c5ffe444dce4eb3bb4ffa0da77cd553f1b906` | `2626a2223c8ed83ed7f852d8cc1c0b1ae8a078ab` |

Verification: CandyStore `.venv/bin/python -m pytest -q tests/test_hook_outcome.py` **32 passed**, `.venv/bin/python -m ruff check candystore/cli.py tests/test_hook_outcome.py` **passed**; Bloodbank `python -m unittest discover -s services/hook-hub/tests -p test_candystore_context.py -q` **9 passed**. Initial system `pytest` lacked `psycopg2` in its interpreter; rerunning under the existing CandyStore `.venv` passed. The previously completed full-suite run (215 tests on disposable Postgres) was not rerun against the live DB.

At **2026-11-17T19:31:13Z**, with the installed CLI and actual live API at `http://127.0.0.1:8683`:

- `candystore context latest --hook-outcome --cwd /tmp` and `--cwd /home/delorenj`: each exit **0**, JSON `{ "status": "skipped", "reason": "no_project" }`. The prior reported failure now skips.
- Actual symlinked handler invoked via `BB_HOOK_CLI=claude BB_HOOK_NATIVE=SessionStart python /home/delorenj/.agents/hooks/hub/concerns.py candystore-context` with native stdin `{"cwd":"/tmp","session_id":"candys73-rollout-probe"}` (repeated for `/home/delorenj`): process exit **0**, `_hook_hub.status=skipped`, `reason=no_project`, `exit_code=0`, empty `stdout` and stderr. No context injection.
- Same installed CLI with `--cwd /home/delorenj/code/33GOD/candystore`: exit **0**, JSON `status=succeeded` with nonempty context. Same actual handler and native stdin for that registered cwd: exit **0**, `_hook_hub.status=succeeded`, reason `handler_completed`, native `stdout` is a Claude `hookSpecificOutput` with `hookEventName=SessionStart` and nonempty `additionalContext`. History content was not copied into this record.
- Explicit `CANDYSTORE_URL=http://127.0.0.1:1` on the installed CLI: exit **1**, empty stdout, stderr diagnostic. Actual handler with that override: process exit **0** (agent continues), `_hook_hub.status=failed`, `reason=handler_exit_nonzero`, `exit_code=1`, empty `stdout`. This is a real closed-loopback transport failure, not a stand-in service.
- Registered CandyStore project queried against the live API with a narrow ISO `--since` instant (2026-11-17T19:31:28.853667+00:00): installed CLI exit **0**, JSON `skipped/no_history`. `--since 1s` is invalid by API contract (error 400); do not use it. The installed handler has no `--since` override; a live *handler* `no_history` case was not asserted. Existing HTTP-boundary and handler fixture tests cover the contract, not live handler history absence. Finding a registered cwd with empty default-window history remains a coverage gap.
- Live hook receipt endpoint `GET http://127.0.0.1:8685/v1/hooks/invocations?handler=candystore-context&limit=500` responded 200; after activation, four receipt invocations were observed: **2 succeeded/handler_completed, 2 skipped/not_first_turn, 0 failed**. This is only an early spot check, **not** the 24-hour AC6 count. The live event API also answered 200 for `GET /events?type=bloodbank.agent.hook.updated&from=2026-11-17T19:30:24Z&limit=1` and returned the expected invocation/executions projection. The native direct handler probes do not publish hook-hub receipts.

## Acceptance status and 24-hour gate

AC1/AC2 no-project outcome and reason: proven live. AC3 no-history: proven installed CLI against live API and handler fixture; live handler case remains a coverage gap. AC4 genuine failure isolation: proven actual handler against a closed loopback port, plus tests. AC5 registered success/native context: proven live. **AC6 remains pending external time and publication coverage**; do not close the ticket from this spot check or misstate “zero failures/day.” Earliest complete post-activation UTC window (with a safe one-minute rollout boundary): **[2026-11-17T19:31:00Z, 2026-11-18T19:31:00Z)**; wait until after the endpoint and receipt publication settle before evaluating. Do not schedule a recurring job.

At/after **2026-11-18T19:31:00Z**, query both the live CandyStore observation API and the live hook-hub receipt endpoint. An executable, bounded recipe (read-only; prints only aggregates) follows. It pages until empty / `next_offset=null`, deduplicates observation revisions by invocation ID, filters on invocation `received_at` inside the window (not observation publication time), and checks for missing publication against the receipt journal. Run after a short publication settling interval, inspect any discrepancy, and retain failed reasons (genuine failures must remain visible):

```bash
python - <<'PY'
import collections, datetime, json, urllib.parse, urllib.request
start, end = '2026-11-17T19:31:00Z', '2026-11-18T19:31:00Z'
stop = (datetime.datetime.fromisoformat(end.replace('Z', '+00:00')) + datetime.timedelta(minutes=10)).isoformat().replace('+00:00', 'Z')
def get(base, params):
    with urllib.request.urlopen(base + '?' + urllib.parse.urlencode(params), timeout=15) as r:
        return json.load(r)
def eligible(inv):
    return start <= inv['received_at'] < end
def outcomes(inv):
    return [(x['status'], x.get('reason')) for x in inv['executions'] if x['handler_id'] == 'candystore-context']
api = 'http://127.0.0.1:8683/events'
latest = {}
offset = 0
while True:
    page = get(api, {'type':'bloodbank.agent.hook.updated', 'from':start, 'to':stop, 'limit':500, 'offset':offset})
    events = page['events']
    for event in events:
        inv = event.get('data', {}).get('invocation', {})
        if inv and eligible(inv):
            key = inv['invocation_id']
            if key not in latest or (inv.get('updated_at', ''), event['time']) > (latest[key].get('updated_at', ''), latest[key]['_event_time']):
                latest[key] = {**inv, '_event_time':event['time']}
    if len(events) < 500: break
    offset += len(events)
print('observation_pages_complete', offset + len(events), 'matched_invocations', len(latest))
observed = {k: outcomes(v) for k,v in latest.items() if outcomes(v)}
print('observation_outcomes', dict(collections.Counter(pair for pairs in observed.values() for pair in pairs)))
hub = 'http://127.0.0.1:8685/v1/hooks/invocations'
receipts = {}
offset = 0
while True:
    page = get(hub, {'handler':'candystore-context','limit':500,'offset':offset})
    for inv in page['items']:
        if eligible(inv): receipts[inv['invocation_id']] = outcomes(inv)
    if page['next_offset'] is None: break
    offset = page['next_offset']
print('receipt_outcomes', dict(collections.Counter(pair for pairs in receipts.values() for pair in pairs)))
print('missing_or_differing_publication', sum(observed.get(k) != v for k,v in receipts.items()))
print('no_project_skips', sum(('skipped','no_project') in v for v in receipts.values()))
print('no_history_skips', sum(('skipped','no_history') in v for v in receipts.values()))
print('failed_reasons', dict(collections.Counter(reason for pairs in receipts.values() for status,reason in pairs if status == 'failed')))
PY
```

Verify `missing_or_differing_publication == 0` and inspect any failed receipt details through `/v1/hooks/invocations/<id>` (do not publish session IDs or history). There must be **zero failed receipts attributable to `no_project` or `no_history`**, not necessarily zero genuine failures. If either skip count is zero, flag coverage; use a registered cwd whose default `/context/latest` result truly has empty `sessions` **and** `decisions` to exercise live handler `no_history`, without altering registry or DB. Report the complete window and totals on the ticket; no Plane state was changed here.
