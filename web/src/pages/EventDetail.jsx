import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { eventTime, getJson } from "../api.js";

const hiddenFields = new Set(["title"]);

export default function EventDetail() {
  const { id } = useParams();
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("loading");

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    getJson(`/events/${id}/summary`)
      .then((payload) => {
        if (cancelled) return;
        setData(payload);
        setStatus("ready");
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (status !== "ready") {
    return <div className="text-sm text-zinc-400">{status}</div>;
  }

  const summary = data.summary || {};
  const raw = data.raw || {};

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">{summary.title || raw.type}</h1>
          <div className="mt-1 font-mono text-xs text-zinc-500">{raw.id}</div>
        </div>
        {raw.correlationid ? (
          <Link
            className="focus-ring rounded-md border border-line px-3 py-2 text-sm text-zinc-300 hover:bg-zinc-800"
            to={`/sessions/${raw.correlationid}`}
          >
            Session
          </Link>
        ) : null}
      </div>

      <section className="rounded-md border border-line bg-panel p-5">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Metric label="Time" value={eventTime(raw.time)} />
          <Metric label="Type" value={raw.type} />
          <Metric label="Producer" value={raw.producer} />
          <Metric label="Domain" value={raw.domain} />
        </div>
      </section>

      <section className="rounded-md border border-line bg-panel p-5">
        <h2 className="text-sm font-semibold text-amber-300">Summary</h2>
        <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(summary)
            .filter(([key]) => !hiddenFields.has(key))
            .map(([key, value]) => (
              <Metric key={key} label={key.replaceAll("_", " ")} value={formatValue(value)} />
            ))}
        </dl>
      </section>

      <details className="rounded-md border border-line bg-panel p-5">
        <summary className="cursor-pointer text-sm font-semibold text-zinc-300">Raw envelope</summary>
        <pre className="mt-4 max-h-[520px] overflow-auto rounded bg-zinc-950 p-4 text-xs leading-5 text-zinc-300">
          {JSON.stringify(raw, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs uppercase text-zinc-500">{label}</dt>
      <dd className="mt-1 break-words text-sm text-zinc-100">{value || "unknown"}</dd>
    </div>
  );
}

function formatValue(value) {
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value ?? "unknown");
}
