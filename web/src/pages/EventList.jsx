import { useEffect, useMemo, useState } from "react";
import { getJson, searchIsRunnable } from "../api.js";
import EventCard from "../components/EventCard.jsx";
import FilterBar from "../components/FilterBar.jsx";
import SearchBar from "../components/SearchBar.jsx";

const SEARCH_DEBOUNCE_MS = 250;

export default function EventList() {
  const [events, setEvents] = useState([]);
  const [total, setTotal] = useState(0);
  const [totalCapped, setTotalCapped] = useState(false);
  const [window_, setWindow] = useState(null);
  const [status, setStatus] = useState("loading");
  const [projects, setProjects] = useState([]);
  const [filters, setFilters] = useState({ from: "", to: "", cli: "", project: "", scope: "" });
  const [search, setSearch] = useState("");
  const [activeSearch, setActiveSearch] = useState("");

  // The search box drives a query over ~900k rows, so what the user is still
  // typing and what has actually been asked for are two different things.
  useEffect(() => {
    const timer = setTimeout(() => setActiveSearch(search), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search]);

  // The registry does not change when a filter does, so this is fetched once
  // rather than riding along with every query.
  useEffect(() => {
    let cancelled = false;
    getJson("/projects?window=7d")
      .then((data) => {
        if (!cancelled) setProjects(data.projects || []);
      })
      .catch(() => {
        if (!cancelled) setProjects([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const query = useMemo(() => {
    // `total=1` because this view renders the count. It comes back capped at
    // 10k (see query.py COUNT_CAP) -- an exact figure over the whole trail
    // costs seconds and reads identically to "10,000+".
    const params = new URLSearchParams({ limit: "100", total: "1" });
    if (filters.from) params.set("from", new Date(filters.from).toISOString());
    if (filters.to) params.set("to", new Date(filters.to).toISOString());
    if (filters.cli) params.set("cli", filters.cli);
    if (filters.project) params.set("project", filters.project);
    if (filters.scope) params.set("scope", filters.scope);
    // A half-typed term is not a search. Sending it would only earn a 400.
    if (searchIsRunnable(activeSearch)) params.set("q", activeSearch.trim());
    return params.toString();
  }, [filters, activeSearch]);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    getJson(`/events?${query}`)
      .then((data) => {
        if (cancelled) return;
        setEvents(data.events || []);
        setTotal(data.total ?? 0);
        setTotalCapped(Boolean(data.total_capped));
        setWindow(data.window || null);
        setStatus("ready");
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [query]);

  const searching = searchIsRunnable(activeSearch);
  const settling = search !== activeSearch;
  // The server applied a window the user did not ask for, so say so. Without
  // this, "no events" and "no events in the last day" look identical -- on an
  // audit trail whose whole point is history, that is the difference between
  // an answer and a lie.
  const defaultWindow = Boolean(window_?.from) && !filters.from && !filters.to;

  return (
    <div className="space-y-4">
      <SearchBar
        value={search}
        onChange={setSearch}
        pending={settling || (searching && status === "loading")}
      />
      <FilterBar filters={filters} onChange={setFilters} projects={projects} />
      <div className="flex items-center justify-between gap-3 text-sm text-zinc-400">
        <span>
          {status !== "ready" ? (
            status
          ) : (
            <>
              {total.toLocaleString()}
              {totalCapped ? "+" : ""} {total === 1 ? "event" : "events"}
              {searching ? (
                <>
                  {" matching "}
                  <span className="font-mono text-zinc-200">{activeSearch.trim()}</span>
                </>
              ) : null}
            </>
          )}
        </span>
        <span className="flex items-center gap-3 text-zinc-500">
          {status === "ready" && defaultWindow ? (
            <span title="Add a From date to search further back">last 24 hours</span>
          ) : null}
          {status === "ready" && (totalCapped || total > events.length) ? (
            <span>showing the {events.length} most recent</span>
          ) : null}
        </span>
      </div>
      <div className="grid gap-3">
        {events.map((event) => (
          <EventCard key={event.id} event={event} />
        ))}
      </div>
      {status === "ready" && events.length === 0 ? (
        <div className="rounded-md border border-line bg-panel p-6 text-sm text-zinc-400">
          {searching
            ? `Nothing matches "${activeSearch.trim()}"${defaultWindow ? " in the last 24 hours" : " with the current filters"}. Search covers the event type, producer, project, working directory, branch, tool name, status and the start of any prompt, arguments or error text.${defaultWindow ? " Set a From date to search the whole trail." : ""}`
            : defaultWindow
              ? "No events in the last 24 hours. Set a From date to look further back."
              : "No events match the current filters."}
        </div>
      ) : null}
    </div>
  );
}
