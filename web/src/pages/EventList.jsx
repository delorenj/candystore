import { useEffect, useMemo, useState } from "react";
import { getJson, searchIsRunnable } from "../api.js";
import EventCard from "../components/EventCard.jsx";
import FilterBar from "../components/FilterBar.jsx";
import SearchBar from "../components/SearchBar.jsx";

const SEARCH_DEBOUNCE_MS = 250;

export default function EventList() {
  const [events, setEvents] = useState([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("loading");
  const [filters, setFilters] = useState({ from: "", to: "", cli: "", project: "", scope: "" });
  const [search, setSearch] = useState("");
  const [activeSearch, setActiveSearch] = useState("");

  // The search box drives a query over ~900k rows, so what the user is still
  // typing and what has actually been asked for are two different things.
  useEffect(() => {
    const timer = setTimeout(() => setActiveSearch(search), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search]);

  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: "100" });
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
        setTotal(data.total || 0);
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

  return (
    <div className="space-y-4">
      <SearchBar
        value={search}
        onChange={setSearch}
        pending={settling || (searching && status === "loading")}
      />
      <FilterBar filters={filters} onChange={setFilters} />
      <div className="flex items-center justify-between gap-3 text-sm text-zinc-400">
        <span>
          {status !== "ready" ? (
            status
          ) : (
            <>
              {total.toLocaleString()} {total === 1 ? "event" : "events"}
              {searching ? (
                <>
                  {" matching "}
                  <span className="font-mono text-zinc-200">{activeSearch.trim()}</span>
                </>
              ) : null}
            </>
          )}
        </span>
        {status === "ready" && total > events.length ? (
          <span className="text-zinc-500">showing the {events.length} most recent</span>
        ) : null}
      </div>
      <div className="grid gap-3">
        {events.map((event) => (
          <EventCard key={event.id} event={event} />
        ))}
      </div>
      {status === "ready" && events.length === 0 ? (
        <div className="rounded-md border border-line bg-panel p-6 text-sm text-zinc-400">
          {searching
            ? `Nothing matches "${activeSearch.trim()}" with the current filters. Search covers the event type, producer, project, working directory, branch, tool name, status and the start of any prompt, arguments or error text.`
            : "No events match the current filters."}
        </div>
      ) : null}
    </div>
  );
}
