import { useEffect, useMemo, useState } from "react";
import { getJson } from "../api.js";
import EventCard from "../components/EventCard.jsx";
import FilterBar from "../components/FilterBar.jsx";

export default function EventList() {
  const [events, setEvents] = useState([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("loading");
  const [filters, setFilters] = useState({ from: "", to: "", cli: "", project: "", scope: "" });

  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: "100" });
    if (filters.from) params.set("from", new Date(filters.from).toISOString());
    if (filters.to) params.set("to", new Date(filters.to).toISOString());
    if (filters.cli) params.set("cli", filters.cli);
    if (filters.project) params.set("project", filters.project);
    if (filters.scope) params.set("scope", filters.scope);
    return params.toString();
  }, [filters]);

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

  return (
    <div className="space-y-4">
      <FilterBar filters={filters} onChange={setFilters} />
      <div className="flex items-center justify-between gap-3 text-sm text-zinc-400">
        <span>{status === "ready" ? `${total} events` : status}</span>
      </div>
      <div className="grid gap-3">
        {events.map((event) => (
          <EventCard key={event.id} event={event} />
        ))}
      </div>
      {status === "ready" && events.length === 0 ? (
        <div className="rounded-md border border-line bg-panel p-6 text-sm text-zinc-400">
          No events match the current filters.
        </div>
      ) : null}
    </div>
  );
}
