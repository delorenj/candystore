import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getJson, searchIsRunnable } from "../api.js";
import ControlBar from "../components/ControlBar.jsx";
import FeedRow from "../components/FeedRow.jsx";
import FoldRow from "../components/FoldRow.jsx";
import SearchBar from "../components/SearchBar.jsx";
import TimelineStrip from "../components/TimelineStrip.jsx";
import {
  chooseBucket,
  feedQuery,
  feedReducer,
  filtersFromParams,
  initialState,
  paramsFromFilters,
  stripQuery,
} from "../state/feedReducer.js";

const SEARCH_DEBOUNCE_MS = 250;

export default function ProjectFeed() {
  const [params, setParams] = useSearchParams();
  const { slug = "" } = useParams();
  const navigate = useNavigate();
  const [state, dispatch] = useReducer(feedReducer, initialState);

  // Reference data: the registry and the two facet vocabularies. None of it
  // changes when a filter does, so it is fetched once rather than riding along
  // with every query.
  const reference = useReference();

  // The URL is the source of truth, so filters are DERIVED from it rather than
  // mirrored into state. Back/forward then work for free, and there is no
  // second copy to fall out of sync.
  const filters = useMemo(() => filtersFromParams(params, slug), [params, slug]);

  const update = useCallback(
    (patch) => {
      const next = { ...filtersFromParams(params, slug), ...patch };
      const query = paramsFromFilters(next).toString();
      // `replace` rather than push: twenty back-presses to escape a session of
      // clicking chips is not navigation, it is a trap.
      if ("project" in patch && patch.project !== slug) {
        const path = patch.project ? `/projects/${patch.project}` : "/projects";
        navigate(query ? `${path}?${query}` : path, { replace: true });
        return;
      }
      setParams(query, { replace: true });
    },
    [params, slug, setParams, navigate]
  );

  // What the user is still typing and what has actually been asked for are two
  // different things when the query runs over ~900k rows.
  const [draftQ, setDraftQ] = useDebouncedQ(filters.q, (next) => update({ q: next }));

  const bucketSeconds = useMemo(
    () => chooseBucket(state.window?.from || filters.from, filters.to),
    [state.window, filters.from, filters.to]
  );

  const query = useMemo(() => feedQuery(filters), [filters]);
  const strip = useMemo(
    () => stripQuery(filters, bucketSeconds),
    [filters, bucketSeconds]
  );

  useFetch(`/events?${query}`, dispatch, "FEED_LOADING", "FEED_LOADED", "feed");
  useFetch(`/summary/timeline?${strip}`, dispatch, "STRIP_LOADING", "STRIP_LOADED", "strip");

  const byId = useMemo(
    () => Object.fromEntries(state.events.map((event) => [event.id, event])),
    [state.events]
  );

  const searching = searchIsRunnable(draftQ);
  const settling = draftQ !== filters.q;
  const defaultWindow = Boolean(state.window?.from) && !filters.from && !filters.to;

  return (
    <div className="space-y-3">
      <ControlBar
        filters={filters}
        projects={reference.projects}
        lenses={reference.lenses}
        classes={reference.classes}
        onChange={update}
        feedStatus={state.feedStatus}
      />

      <SearchBar
        value={draftQ}
        onChange={setDraftQ}
        pending={settling || (searching && state.feedStatus === "loading")}
      />

      <TimelineStrip
        buckets={state.buckets}
        series={state.series}
        bucketSeconds={state.bucketSeconds}
        colors={reference.colors}
        status={state.stripStatus}
      />

      <div className="flex items-center justify-between gap-3 text-xs text-zinc-500">
        <span>
          {state.feedStatus === "error" ? (
            <span className="text-red-400">{state.error}</span>
          ) : (
            <>
              {(state.total ?? 0).toLocaleString()}
              {state.totalCapped ? "+" : ""} events
              {state.folded ? ` · ${state.folded.toLocaleString()} tool calls folded` : ""}
            </>
          )}
        </span>
        <span className="flex items-center gap-3">
          {defaultWindow ? <span>last 24 hours</span> : null}
          <span>{state.rows.length} rows</span>
        </span>
      </div>

      <div className="divide-y divide-line/60 rounded-md border border-line bg-panel">
        {state.rows.map((row) =>
          row.kind === "fold" ? (
            <FoldRow
              key={row.id}
              fold={row}
              members={(row.member_ids || []).map((id) => byId[id]).filter(Boolean)}
              expanded={Boolean(state.expanded[row.id])}
              onToggle={(id) => dispatch({ type: "FOLD_TOGGLED", id })}
              colors={reference.colors}
            />
          ) : (
            <FeedRow key={row.id} event={row} colors={reference.colors} />
          )
        )}
        {state.feedStatus === "ready" && !state.rows.length ? (
          <EmptyState filters={filters} defaultWindow={defaultWindow} searching={searching} />
        ) : null}
      </div>
    </div>
  );
}

function EmptyState({ filters, defaultWindow, searching }) {
  // "Nothing here" and "nothing here in the last day" look identical unless one
  // of them says so -- and on an audit trail, that is the difference between an
  // answer and a lie.
  const scope = [
    filters.lens ? `the ${filters.lens} lens` : null,
    filters.project ? `project ${filters.project}` : null,
  ]
    .filter(Boolean)
    .join(" in ");

  return (
    <div className="p-6 text-sm text-zinc-400">
      {searching
        ? `Nothing matches "${filters.q}"${defaultWindow ? " in the last 24 hours" : ""}.`
        : scope
          ? `No events for ${scope}${defaultWindow ? " in the last 24 hours" : ""}.`
          : "No events match the current filters."}
      {defaultWindow ? " Pick a longer window to look further back." : ""}
    </div>
  );
}

/** Registry, lenses and provenance classes. Fetched once; a filter change does
 * not alter any of them. */
function useReference() {
  const [state, dispatch] = useReducer(
    (current, action) => ({ ...current, ...action }),
    { projects: [], lenses: [], classes: [], colors: {} }
  );

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getJson("/projects?window=24h").catch(() => ({ projects: [] })),
      getJson("/lenses?window=24h").catch(() => ({ lenses: [] })),
      getJson("/classes?window=24h").catch(() => ({ classes: [] })),
    ]).then(([projects, lenses, classes]) => {
      if (cancelled) return;
      dispatch({
        projects: projects.projects || [],
        lenses: lenses.lenses || [],
        classes: classes.classes || [],
        colors: Object.fromEntries(
          (classes.classes || []).map((entry) => [entry.class, entry.color])
        ),
      });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}

/** One request per URL, cancelled on change so a slow response cannot overwrite
 * a newer one. */
function useFetch(path, dispatch, loadingType, loadedType, scope) {
  useEffect(() => {
    let cancelled = false;
    dispatch({ type: loadingType });
    getJson(path)
      .then((payload) => {
        if (!cancelled) dispatch({ type: loadedType, payload });
      })
      .catch((error) => {
        if (!cancelled) dispatch({ type: "ERROR", scope, error: String(error.message || error) });
      });
    return () => {
      cancelled = true;
    };
  }, [path, dispatch, loadingType, loadedType, scope]);
}

/** Local draft text, pushed into the URL only once typing settles. */
function useDebouncedQ(committed, commit) {
  const [draft, setDraft] = useReducer((_, next) => next, committed);
  const latest = useRef(commit);
  latest.current = commit;

  useEffect(() => setDraft(committed), [committed]);

  useEffect(() => {
    if (draft === committed) return undefined;
    // A term the trigram index cannot serve earns a 400, so a half-typed word
    // is held back rather than sent (api.SEARCH_MIN_TERM).
    if (draft && !searchIsRunnable(draft)) return undefined;
    const timer = setTimeout(() => latest.current(draft), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, committed]);

  return [draft, setDraft];
}
