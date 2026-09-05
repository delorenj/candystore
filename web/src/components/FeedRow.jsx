import { Link } from "react-router-dom";
import { PROVENANCE_FALLBACK_COLOR } from "../state/feedReducer.js";

/**
 * One event, one fixed-height row.
 *
 * Two encodings on two channels, sharing no hue:
 *   - the DOT carries provenance (which agent, service or webhook)
 *   - the left STRIPE and glyph carry outcome (did it work)
 * If a dot could be red, red would stop meaning bad -- which is why the
 * provenance palette contains no red and no green (see query.PROVENANCE_CLASSES).
 *
 * Paths are middle-truncated, not end-truncated: in this corpus the
 * discriminating token is at the end (`.../candystore/candystore/query.py`).
 */

function middleTruncate(text, max = 92) {
  if (!text || text.length <= max) return text;
  const head = Math.ceil((max - 1) * 0.4);
  return `${text.slice(0, head)}…${text.slice(text.length - (max - 1 - head))}`;
}

function timeOfDay(iso) {
  if (!iso) return "--:--:--";
  return new Date(iso).toLocaleTimeString([], { hour12: false });
}

export default function FeedRow({ event, colors }) {
  const row = event.summary?.row || {};
  const dot = colors[row.class] || PROVENANCE_FALLBACK_COLOR;
  const failed = row.ok === false;

  return (
    <Link
      to={`/events/${event.id}`}
      className={[
        "focus-ring group grid grid-cols-[auto_auto_10rem_1fr_auto] items-center gap-3",
        "border-l-2 px-3 py-1.5 text-sm hover:bg-zinc-800/60",
        failed ? "border-l-red-500 bg-red-500/5" : "border-l-transparent",
      ].join(" ")}
      title={row.body || row.headline}
    >
      <span className="font-mono text-xs tabular-nums text-zinc-500">
        {timeOfDay(event.time)}
      </span>
      <span
        aria-hidden="true"
        className="h-2 w-2 shrink-0 rounded-full"
        style={{ backgroundColor: dot }}
      />
      <span className="truncate text-xs text-zinc-400">{row.actor_label}</span>
      <span className="min-w-0 truncate">
        <span className={failed ? "text-red-300" : "text-zinc-100"}>
          {failed ? "✗ " : ""}
          {row.headline}
        </span>
        {row.body ? (
          <span className="ml-2 font-mono text-xs text-zinc-500">
            {middleTruncate(row.body)}
          </span>
        ) : null}
      </span>
      <span className="shrink-0 font-mono text-xs tabular-nums text-zinc-600">
        {row.duration_ms != null ? formatDuration(row.duration_ms) : ""}
      </span>
    </Link>
  );
}

function formatDuration(ms) {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 60_000)}m`;
}
