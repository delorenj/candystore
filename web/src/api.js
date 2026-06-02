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
