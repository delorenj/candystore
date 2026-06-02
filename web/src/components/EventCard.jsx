import { Link } from "react-router-dom";
import { eventTime, shortId } from "../api.js";

const cliColors = {
  claude: "border-amber-400",
  copilot: "border-blue-400",
  gemini: "border-teal-400",
  opencode: "border-violet-400",
};

export default function EventCard({ event }) {
  const cli = event.cli || event.actor?.cli || "unknown";
  const color = cliColors[cli] || "border-zinc-600";
  const summary = event.summary || {};

  return (
    <Link
      to={`/events/${event.id}`}
      className={`focus-ring block rounded-md border border-line border-l-4 bg-panel p-4 transition hover:border-zinc-500 hover:bg-zinc-800 ${color}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-zinc-100">
            {summary.title || event.type}
          </div>
          <div className="mt-1 truncate font-mono text-xs text-zinc-500">{event.type}</div>
        </div>
        <div className="shrink-0 text-right text-xs text-zinc-500">
          <div>{eventTime(event.time)}</div>
          <div className="font-mono">{shortId(event.id)}</div>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-zinc-400">
        <span className="rounded bg-zinc-900 px-2 py-1">{cli}</span>
        <span className="rounded bg-zinc-900 px-2 py-1">{event.project || "unknown"}</span>
        <span className="rounded bg-zinc-900 px-2 py-1">{event.domain}</span>
      </div>
    </Link>
  );
}
