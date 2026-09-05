/**
 * Feed state for /projects/:slug.
 *
 * One reducer in one page-level context, not a store. TanStack Store was
 * evaluated and rejected (see AGENTS.md): the hard part here is server-state
 * lifecycle -- cancellation, keep-previous, and three derived views off one row
 * array -- which a client-state store solves none of.
 *
 * The URL is the query. The project is the path segment and every other filter
 * round-trips through the querystring,
 * which is what buys shareability, back/forward and agent-consumable deep links
 * WITHOUT a grammar. That is the whole no-query-language bet: a link is the
 * saved view, and `?project=&lens=&class=&tools=0&from=&to=&q=` is the same
 * shape the API takes, so "copy as API call" is a formatting change rather than
 * a translation.
 */

export const PROVENANCE_FALLBACK_COLOR = "#9ca3af";

export const initialState = {
  // Mirrors the querystring exactly. `classes` is an array because multi-select
  // within one facet is OR; across facets it is AND.
  filters: { project: "", lens: "", classes: [], tools: true, from: "", to: "", q: "" },
  events: [],
  rows: [],
  total: null,
  totalCapped: false,
  folded: 0,
  window: null,
  buckets: [],
  series: [],
  bucketSeconds: 60,
  // Two independent flags. One spinner for both would mean a slow strip makes
  // the feed look stale, and vice versa.
  feedStatus: "loading",
  stripStatus: "loading",
  error: null,
  expanded: {},
};

export function feedReducer(state, action) {
  switch (action.type) {
    case "FILTERS_SET":
      return {
        ...state,
        filters: { ...state.filters, ...action.filters },
        // Collapse state belongs to the rows it describes, and those are about
        // to be replaced.
        expanded: {},
      };

    case "FEED_LOADING":
      return { ...state, feedStatus: "loading", error: null };

    case "FEED_LOADED":
      return {
        ...state,
        feedStatus: "ready",
        events: action.payload.events || [],
        rows: action.payload.rows || action.payload.events || [],
        total: action.payload.total ?? null,
        totalCapped: Boolean(action.payload.total_capped),
        folded: action.payload.folded || 0,
        window: action.payload.window || null,
      };

    case "STRIP_LOADING":
      return { ...state, stripStatus: "loading" };

    case "STRIP_LOADED":
      return {
        ...state,
        stripStatus: "ready",
        buckets: action.payload.buckets || [],
        series: action.payload.series || [],
        bucketSeconds: action.payload.bucket_seconds || 60,
      };

    case "ERROR":
      return {
        ...state,
        [action.scope === "strip" ? "stripStatus" : "feedStatus"]: "error",
        error: action.error || "request failed",
      };

    case "FOLD_TOGGLED":
      return {
        ...state,
        expanded: { ...state.expanded, [action.id]: !state.expanded[action.id] },
      };

    default:
      return state;
  }
}

/** Querystring -> filters. The URL is the source of truth, so this runs on
 * every navigation including back/forward. */
export function filtersFromParams(params, slug = "") {
  const classes = (params.get("class") || "").split(",").filter(Boolean);
  return {
    // The project lives in the PATH (`/projects/candystore`), not the query.
    // One canonical place, so a link is unambiguous and the screen has a real
    // identity rather than being a querystring on a generic page.
    project: slug || "",
    lens: params.get("lens") || "",
    classes,
    // Absent means on. Only an explicit `tools=0` hides them, so a bare link
    // shows everything.
    tools: params.get("tools") !== "0",
    from: params.get("from") || "",
    to: params.get("to") || "",
    q: params.get("q") || "",
  };
}

/** Filters -> querystring. Empty values are omitted rather than written as
 * blanks, so a default view has a clean, short, shareable URL. */
export function paramsFromFilters(filters) {
  const params = new URLSearchParams();
  if (filters.lens) params.set("lens", filters.lens);
  if (filters.classes.length) params.set("class", filters.classes.join(","));
  if (!filters.tools) params.set("tools", "0");
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (filters.q) params.set("q", filters.q);
  return params;
}

/**
 * The feed request. `total=1` because the header renders the count; it comes
 * back capped at 10k, which reads identically to an exact figure nobody can
 * verify and costs seconds less.
 */
export function feedQuery(filters, { limit = 200 } = {}) {
  const params = paramsFromFilters(filters);
  if (filters.project) params.set("project", filters.project);
  params.set("limit", String(limit));
  params.set("total", "1");
  return params.toString();
}

/**
 * The strip request. Deliberately does NOT carry `tools`: the chart counts
 * every event in scope including the ones the feed is folding, so collapsing
 * the feed never changes the shape above it.
 */
export function stripQuery(filters, bucketSeconds) {
  const params = paramsFromFilters(filters);
  if (filters.project) params.set("project", filters.project);
  params.delete("tools");
  params.set("bucket", String(bucketSeconds));
  params.set("group", "class");
  return params.toString();
}

/** Bucket width that yields roughly `target` columns across the window. */
export function chooseBucket(fromISO, toISO, target = 40) {
  const allowed = [1, 60, 300, 1800, 3600];
  const end = toISO ? Date.parse(toISO) : Date.now();
  const start = fromISO ? Date.parse(fromISO) : end - 24 * 3600 * 1000;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return 3600;
  const ideal = (end - start) / 1000 / target;
  // The smallest allowed width that does not overshoot the target column
  // count, so the strip never renders thousands of 1px bars.
  return allowed.find((width) => width >= ideal) ?? allowed[allowed.length - 1];
}
