import { useEffect, useRef } from "react";
import { SEARCH_MIN_TERM, searchTerms } from "../api.js";

export default function SearchBar({ value, onChange, pending }) {
  const inputRef = useRef(null);

  // `/` focuses search, the convention everywhere from Gmail to GitHub. Guarded
  // so it only fires when the user is not already typing into something.
  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      const tag = target?.tagName;
      if (target?.isContentEditable || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
        return;
      }
      event.preventDefault();
      inputRef.current?.focus();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const onInputKeyDown = (event) => {
    if (event.key !== "Escape") return;
    if (value) {
      onChange("");
    } else {
      inputRef.current?.blur();
    }
  };

  const short = searchTerms(value).filter((term) => term.length < SEARCH_MIN_TERM);

  return (
    <search className="grid gap-1.5">
      <div className="relative">
        <MagnifierIcon />
        <input
          ref={inputRef}
          type="search"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onInputKeyDown}
          aria-label="Search events"
          placeholder="Search events - tool, file path, branch, error, or paste an event / session id"
          className="focus-ring w-full rounded-md border border-line bg-zinc-900 py-2.5 pl-10 pr-24 text-sm text-zinc-100 placeholder:text-zinc-600 [&::-webkit-search-cancel-button]:appearance-none"
        />
        <div className="absolute inset-y-0 right-2 flex items-center gap-2">
          {pending ? <Spinner /> : null}
          {value ? (
            <button
              type="button"
              onClick={() => {
                onChange("");
                inputRef.current?.focus();
              }}
              aria-label="Clear search"
              className="focus-ring rounded px-2 py-1 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
            >
              Clear
            </button>
          ) : (
            <kbd className="hidden rounded border border-line px-1.5 py-0.5 font-mono text-xs text-zinc-600 sm:block">
              /
            </kbd>
          )}
        </div>
      </div>
      {short.length ? (
        <p className="text-xs text-zinc-500">
          Keep typing - {short.length === 1 ? "the term" : "every term"} needs{" "}
          {SEARCH_MIN_TERM}+ characters ({short.map((term) => `"${term}"`).join(", ")}).
        </p>
      ) : null}
    </search>
  );
}

function MagnifierIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500"
    >
      <circle cx="8.5" cy="8.5" r="5.5" />
      <path d="M12.8 12.8 17 17" strokeLinecap="round" />
    </svg>
  );
}

function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-700 border-t-amber-400"
    />
  );
}
