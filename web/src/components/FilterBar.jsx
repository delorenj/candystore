const presets = [
  { label: "Today", hours: 24 },
  { label: "7d", hours: 24 * 7 },
  { label: "30d", hours: 24 * 30 },
];

const cliOptions = ["claude", "copilot", "gemini", "opencode"];

const scopeOptions = [
  { value: "agent", label: "agent" },
  { value: "agent.invocation", label: "agent.invocation" },
  { value: "agent.online", label: "agent.online" },
  { value: "cli", label: "cli" },
  { value: "cli.session", label: "cli.session" },
  { value: "conversation", label: "conversation" },
  { value: "conversation.message", label: "conversation.message" },
  { value: "conversation.turn", label: "conversation.turn" },
  { value: "repo", label: "repo" },
  { value: "repo.decision", label: "repo.decision" },
  { value: "repo.intake", label: "repo.intake" },
  { value: "repo.task", label: "repo.task" },
  { value: "tool", label: "tool" },
  { value: "tool.tool_call", label: "tool.tool_call" },
];

function isoLocal(hoursAgo) {
  const date = new Date(Date.now() - hoursAgo * 60 * 60 * 1000);
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

export default function FilterBar({ filters, onChange }) {
  const update = (key, value) => onChange({ ...filters, [key]: value });
  const setPreset = (hours) => onChange({ ...filters, from: isoLocal(hours), to: "" });

  const toggleScope = (value) => {
    const current = filters.scope ? filters.scope.split(",") : [];
    const next = current.includes(value)
      ? current.filter((v) => v !== value)
      : [...current, value];
    update("scope", next.join(","));
  };

  return (
    <div className="grid gap-3 border-b border-line pb-4 lg:grid-cols-[1fr_auto]">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="grid gap-1 text-xs font-medium uppercase text-zinc-500">
          From
          <input
            type="datetime-local"
            className="focus-ring rounded-md border border-line bg-zinc-900 px-3 py-2 text-sm text-zinc-100"
            value={filters.from}
            onChange={(event) => update("from", event.target.value)}
          />
        </label>
        <label className="grid gap-1 text-xs font-medium uppercase text-zinc-500">
          To
          <input
            type="datetime-local"
            className="focus-ring rounded-md border border-line bg-zinc-900 px-3 py-2 text-sm text-zinc-100"
            value={filters.to}
            onChange={(event) => update("to", event.target.value)}
          />
        </label>
        <label className="grid gap-1 text-xs font-medium uppercase text-zinc-500">
          CLI
          <select
            className="focus-ring rounded-md border border-line bg-zinc-900 px-3 py-2 text-sm text-zinc-100"
            value={filters.cli}
            onChange={(event) => update("cli", event.target.value)}
          >
            <option value="">All</option>
            {cliOptions.map((cli) => (
              <option key={cli} value={cli}>
                {cli}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1 text-xs font-medium uppercase text-zinc-500">
          Project
          <input
            type="text"
            className="focus-ring rounded-md border border-line bg-zinc-900 px-3 py-2 text-sm text-zinc-100"
            value={filters.project}
            onChange={(event) => update("project", event.target.value)}
          />
        </label>
      </div>

      {/* Scope multi-select */}
      <div className="lg:col-span-full">
        <div className="grid gap-1">
          <span className="text-xs font-medium uppercase text-zinc-500">Scope / Level</span>
          <div className="flex flex-wrap gap-2">
            {scopeOptions.map((opt) => {
              const active = filters.scope?.split(",").includes(opt.value);
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => toggleScope(opt.value)}
                  className={[
                    "focus-ring rounded-md border px-2.5 py-1.5 text-xs transition",
                    active
                      ? "border-amber-500/50 bg-amber-500/10 text-amber-300"
                      : "border-line bg-zinc-900 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200",
                  ].join(" ")}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="flex items-end gap-2">
        {presets.map((preset) => (
          <button
            key={preset.label}
            type="button"
            className="focus-ring rounded-md border border-line px-3 py-2 text-sm text-zinc-300 hover:bg-zinc-800"
            onClick={() => setPreset(preset.hours)}
          >
            {preset.label}
          </button>
        ))}
      </div>
    </div>
  );
}
