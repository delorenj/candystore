import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { eventTime, getJson } from "../api.js";

const hiddenFields = new Set([
  "title",
  "row",
  "prompt_preview",
  "reply_preview",
  "message_preview",
  "transcript_preview",
]);

function firstString(...candidates) {
  for (const value of candidates) {
    if (typeof value === "string" && value.trim()) {
      return value;
    }
  }
  return null;
}

function extractDialog(rawData, summary) {
  const data = rawData && typeof rawData === "object" ? rawData : {};
  const payload = data.payload && typeof data.payload === "object" ? data.payload : {};
  const extra = payload.extra && typeof payload.extra === "object" ? payload.extra : {};
  const context = data.context && typeof data.context === "object" ? data.context : {};
  const handoff = data.handoff && typeof data.handoff === "object" ? data.handoff : {};

  let historyPrompt = null;
  if (Array.isArray(extra.conversation_history)) {
    for (let i = extra.conversation_history.length - 1; i >= 0; i -= 1) {
      const entry = extra.conversation_history[i];
      if (entry && entry.role === "user" && typeof entry.content === "string" && entry.content.trim()) {
        historyPrompt = entry.content;
        break;
      }
    }
  }

  const prompt = firstString(
    data.prompt_text,
    data.prompt,
    data.user_message,
    data.user_input,
    data.user_prompt,
    payload.prompt_text,
    payload.prompt,
    payload.user_message,
    payload.user_input,
    payload.user_prompt,
    payload.child_goal,
    extra.user_message,
    extra.prompt_text,
    extra.prompt,
    extra.user_input,
    extra.user_prompt,
    historyPrompt,
    context.prompt,
    context.user_message,
    summary?.prompt_preview,
  );

  const reply = firstString(
    data.reply_text,
    data.last_assistant_message,
    data.final_response,
    data.response,
    payload.reply_text,
    payload.last_assistant_message,
    payload.prompt_response,
    payload.final_response,
    payload.response,
    payload.child_summary,
    extra.final_response,
    extra.reply_text,
    extra.response,
    extra.last_assistant_message,
    handoff.summary,
    summary?.reply_preview,
  );

  const transcript = firstString(
    data.transcript_text,
    payload.transcript_text,
    summary?.transcript_preview,
  );

  const message = firstString(
    data.text,
    data.message,
    payload.text,
    payload.message,
    summary?.message_preview,
  );

  const items = [];
  if (prompt) items.push({ label: "Prompt", text: prompt });
  if (reply) items.push({ label: "Reply", text: reply });
  if (transcript && transcript !== prompt && transcript !== reply) {
    items.push({ label: "Transcript", text: transcript });
  }
  if (message && message !== prompt && message !== reply && message !== transcript) {
    items.push({ label: "Message", text: message });
  }
  return items;
}

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
  const dialogItems = extractDialog(raw.data, summary);

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

      {dialogItems.length > 0 ? (
        <section className="space-y-4 rounded-md border border-line bg-panel p-5">
          <h2 className="text-sm font-semibold text-amber-300">Dialog</h2>
          {dialogItems.map((item) => (
            <div key={item.label} className="space-y-1.5">
              <div className="text-xs font-medium uppercase tracking-wide text-zinc-400">
                {item.label}
              </div>
              <div className="max-h-[480px] overflow-auto whitespace-pre-wrap break-words rounded border border-zinc-800 bg-zinc-950/80 p-4 text-sm leading-relaxed text-zinc-100">
                {item.text}
              </div>
            </div>
          ))}
        </section>
      ) : null}

      <section className="rounded-md border border-line bg-panel p-5">
        <h2 className="text-sm font-semibold text-amber-300">Summary</h2>
        <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(summary)
            .filter(([key, value]) => !hiddenFields.has(key) && value !== null && value !== undefined)
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

