import { PROVENANCE_FALLBACK_COLOR } from "../state/feedReducer.js";

/**
 * Everything that changes what you see, in one sticky band -- and a permanent
 * strip that states the active modes.
 *
 * The state strip is borrowed from k9s and earns its pixels: with a lens on, a
 * class selected and tool calls hidden, three separate decisions are shaping
 * the feed, and none of them should require opening a menu to recall.
 */

const WINDOWS = [
  { label: "1h", value: "-1h" },
  { label: "24h", value: "" },
  { label: "7d", value: "-7d" },
  { label: "30d", value: "-30d" },
];

export default function ControlBar({
  filters,
  projects,
  lenses,
  classes,
  onChange,
  feedStatus,
}) {
  const set = (patch) => onChange(patch);

  const toggleClass = (name) => {
    const next = filters.classes.includes(name)
      ? filters.classes.filter((value) => value !== name)
      : [...filters.classes, name];
    set({ classes: next });
  };

  const active = [
    filters.project || "all projects",
    filters.lens || "all events",
    filters.tools ? "tools:on" : "tools:off",
    filters.classes.length ? filters.classes.join("+") : null,
    WINDOWS.find((w) => w.value === filters.from)?.label || "custom",
  ].filter(Boolean);

  return (
    <div className="sticky top-0 z-10 space-y-2 border-b border-line bg-ink/95 pb-2 pt-1 backdrop-blur">
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="Project"
          className="focus-ring rounded-md border border-line bg-zinc-900 px-2 py-1.5 text-sm text-zinc-100"
          value={filters.project}
          onChange={(event) => set({ project: event.target.value })}
        >
          <option value="">All projects</option>
          {projects.map((project) => (
            <option key={project.slug} value={project.slug}>
              {project.name}
              {project.count ? ` (${project.count.toLocaleString()})` : ""}
            </option>
          ))}
        </select>

        {WINDOWS.map((window) => (
          <button
            key={window.label}
            type="button"
            onClick={() => set({ from: window.value, to: "" })}
            className={[
              "focus-ring rounded-md border px-2.5 py-1.5 text-xs",
              filters.from === window.value
                ? "border-amber-500/50 bg-amber-500/10 text-amber-300"
                : "border-line bg-zinc-900 text-zinc-400 hover:bg-zinc-800",
            ].join(" ")}
          >
            {window.label}
          </button>
        ))}

        <label className="focus-ring flex cursor-pointer items-center gap-2 rounded-md border border-line bg-zinc-900 px-2.5 py-1.5 text-xs text-zinc-300">
          <input
            type="checkbox"
            checked={filters.tools}
            onChange={(event) => set({ tools: event.target.checked })}
            className="accent-amber-400"
          />
          Show tool calls
        </label>

        <span className="ml-auto font-mono text-xs text-zinc-600">
          {active.join(" · ")}
          {feedStatus === "loading" ? " · …" : ""}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {lenses.map((lens) => (
          <button
            key={lens.lens}
            type="button"
            title={lens.description}
            onClick={() => set({ lens: filters.lens === lens.lens ? "" : lens.lens })}
            className={[
              "focus-ring rounded-full border px-2.5 py-1 text-xs transition",
              filters.lens === lens.lens
                ? "border-amber-500/50 bg-amber-500/10 text-amber-300"
                : "border-line bg-zinc-900 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200",
            ].join(" ")}
          >
            {lens.label}
            <span className="ml-1.5 tabular-nums text-zinc-600">
              {lens.count.toLocaleString()}
            </span>
          </button>
        ))}

        <span className="mx-1 h-4 w-px bg-line" />

        {classes
          .filter((entry) => entry.count > 0)
          .map((entry) => {
            const on = filters.classes.includes(entry.class);
            const partial = entry.coverage !== "complete";
            return (
              <button
                key={entry.class}
                type="button"
                // The coverage caveat surfaces here, not only in a ticket. A
                // dot whose gaps are undocumented lies by omission.
                title={partial ? `${entry.label} — incomplete: ${entry.coverage}` : entry.label}
                onClick={() => toggleClass(entry.class)}
                className={[
                  "focus-ring flex items-center gap-1.5 rounded-full border px-2 py-1 text-xs",
                  on
                    ? "border-zinc-400 bg-zinc-800 text-zinc-100"
                    : "border-line bg-zinc-900 text-zinc-500 hover:bg-zinc-800",
                ].join(" ")}
              >
                <span
                  aria-hidden="true"
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: entry.color || PROVENANCE_FALLBACK_COLOR }}
                />
                {entry.label}
                {partial ? <span className="text-amber-500/80">*</span> : null}
                <span className="tabular-nums text-zinc-600">
                  {entry.count.toLocaleString()}
                </span>
              </button>
            );
          })}
      </div>
    </div>
  );
}
