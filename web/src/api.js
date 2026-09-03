const API = import.meta.env.VITE_API_URL || "";

export async function getJson(path) {
  const response = await fetch(`${API}${path}`);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

export function eventTime(value) {
  if (!value) return "unknown";
  return new Date(value).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function shortId(value) {
  return value ? `${value.slice(0, 8)}...${value.slice(-6)}` : "unknown";
}

// The API refuses any term shorter than this, because the trigram index that
// backs search cannot answer a pattern with no complete 3-gram and the query
// would degrade to a full scan of the whole trail (candystore/query.py).
// Mirroring the rule here means search-as-you-type simply holds off while you
// are still typing, instead of firing requests that come back 400.
export const SEARCH_MIN_TERM = 3;

export function searchTerms(value) {
  return (value || "").trim().split(/\s+/).filter(Boolean);
}

export function searchIsRunnable(value) {
  const terms = searchTerms(value);
  return terms.length > 0 && terms.every((term) => term.length >= SEARCH_MIN_TERM);
}
