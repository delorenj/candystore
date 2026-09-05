import FeedRow from "./FeedRow.jsx";
import { PROVENANCE_FALLBACK_COLOR } from "../state/feedReducer.js";

/**
 * A run of consecutive tool calls, collapsed to one row.
 *
 * The count is the primary scan token, so it is bold and tabular. It expands
 * IN PLACE rather than navigating -- a fold is a rendering of a range, not a
 * durable entity, and there is nothing to navigate to.
 *
 * There is deliberately no error badge, because there can never be an error
 * inside a fold: a failure terminates a run and gets its own row
 * (query.collapse_tool_runs). That is what makes hiding tool calls safe.
 */

function span(from, to) {
  const start = Date.parse(from);
  const end = Date.parse(to);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return "";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

export default function FoldRow({ fold, members, expanded, onToggle, colors }) {
  const dot = colors[fold.class] || PROVENANCE_FALLBACK_COLOR;
  const tools = Object.entries(fold.tools || {});

  return (
    <>
      <button
        type="button"
        onClick={() => onToggle(fold.id)}
        aria-expanded={expanded}
        className="focus-ring grid w-full grid-cols-[auto_auto_10rem_1fr_auto] items-center gap-3 border-l-2 border-l-transparent px-3 py-1.5 text-left text-sm hover:bg-zinc-800/60"
      >
        <span className="w-[8ch] font-mono text-xs text-zinc-600">
          {expanded ? "▾" : "▸"}
        </span>
        <span
          aria-hidden="true"
          className="h-2 w-2 shrink-0 rounded-full opacity-60"
          style={{ backgroundColor: dot }}
        />
        <span className="font-mono text-sm font-semibold tabular-nums text-zinc-300">
          {fold.count.toLocaleString()} calls
        </span>
        <span className="min-w-0 truncate text-xs text-zinc-500">
          {tools.map(([name, count]) => `${name}×${count}`).join(" · ")}
          {fold.other_tools ? ` · +${fold.other_tools} more` : ""}
        </span>
        <span className="shrink-0 font-mono text-xs tabular-nums text-zinc-600">
          {span(fold.from, fold.to)}
        </span>
      </button>
      {expanded ? (
        <div className="border-l-2 border-l-zinc-700 bg-zinc-900/40">
          {members.map((event) => (
            <FeedRow key={event.id} event={event} colors={colors} />
          ))}
        </div>
      ) : null}
    </>
  );
}
