import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { eventTime, getJson, shortId } from "../api.js";

export default function SessionTimeline() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [lookup, setLookup] = useState(id || "");
  const [events, setEvents] = useState([]);
  const [summary, setSummary] = useState(null);
  const [status, setStatus] = useState(id ? "loading" : "idle");

  useEffect(() => {
    setLookup(id || "");
    if (!id) return;

    let cancelled = false;
    setStatus("loading");
    Promise.all([getJson(`/sessions/${id}`), getJson(`/sessions/${id}/summary`)])
      .then(([timeline, report]) => {
        if (cancelled) return;
        setEvents(timeline.events || []);
        setSummary(report);
        setStatus("ready");
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  function submit(event) {
    event.preventDefault();
    if (lookup.trim()) navigate(`/sessions/${lookup.trim()}`);
  }

  return (
    <div className="space-y-5">
      <form onSubmit={submit} className="flex flex-col gap-3 border-b border-line pb-4 sm:flex-row">
        <input
          className="focus-ring min-w-0 flex-1 rounded-md border border-line bg-zinc-900 px-3 py-2 text-sm text-zinc-100"
          value={lookup}
          onChange={(event) => setLookup(event.target.value)}
          placeholder="Correlation ID"
        />
        <button
          type="submit"
          className="focus-ring rounded-md bg-amber-500 px-4 py-2 text-sm font-medium text-zinc-950 hover:bg-amber-400"
        >
          Open
        </button>
      </form>

      {summary ? (
        <section className="grid gap-3 rounded-md border border-line bg-panel p-5 sm:grid-cols-2 lg:grid-cols-5">
          <Metric label="Events" value={summary.events_count} />
          <Metric label="CLI" value={summary.cli} />
          <Metric label="Project" value={summary.project} />
          <Metric label="Turns" value={summary.turns} />
          <Metric label="Tools" value={summary.tools_invoked} />
        </section>
      ) : null}

      <div className="text-sm text-zinc-500">{status}</div>

      <ol className="space-y-3">
        {events.map((event, index) => (
          <li key={event.id} className="grid grid-cols-[36px_1fr] gap-3">
            <div className="flex flex-col items-center">
              <span className="grid h-7 w-7 place-items-center rounded-full border border-line bg-zinc-900 text-xs text-zinc-400">
                {index + 1}
              </span>
              {index < events.length - 1 ? <span className="mt-2 h-full w-px bg-line" /> : null}
            </div>
            <div className="rounded-md border border-line bg-panel p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <div className="text-sm font-medium text-zinc-100">
                    {event.summary?.title || event.type}
                  </div>
                  <div className="mt-1 font-mono text-xs text-zinc-500">{event.type}</div>
                </div>
                <div className="text-right text-xs text-zinc-500">
                  <div>{eventTime(event.time)}</div>
                  <div className="font-mono">{shortId(event.id)}</div>
                </div>
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div>
      <dt className="text-xs uppercase text-zinc-500">{label}</dt>
      <dd className="mt-1 break-words text-sm text-zinc-100">{value ?? "unknown"}</dd>
    </div>
  );
}
