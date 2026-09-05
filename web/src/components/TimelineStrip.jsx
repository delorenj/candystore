import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { PROVENANCE_FALLBACK_COLOR } from "../state/feedReducer.js";

/**
 * The strip above the feed: a stacked bar histogram, coloured by provenance
 * class.
 *
 * Stacked by CLASS rather than by event type because 96% of the trail is one
 * type, and stacking by type renders a single solid block that answers nothing.
 *
 * It prints its bucket width verbatim ("1 min per column"), which is Splunk's
 * wording and the thing that stops a histogram from quietly misrepresenting its
 * own resolution.
 *
 * The counts include tool calls the feed may be folding -- the server does not
 * even accept a `tools` parameter here -- so collapsing the feed never changes
 * the shape of the chart above it.
 */

function widthLabel(seconds) {
  if (seconds < 60) return `${seconds} sec per column`;
  if (seconds < 3600) return `${seconds / 60} min per column`;
  return `${seconds / 3600} hr per column`;
}

function tick(iso, bucketSeconds) {
  const date = new Date(iso);
  if (bucketSeconds >= 3600) {
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit" });
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

export default function TimelineStrip({ buckets, series, bucketSeconds, colors, status }) {
  const data = buckets.map((bucket) => ({
    bucket: bucket.bucket,
    ...bucket.series,
  }));
  const total = buckets.reduce((sum, bucket) => sum + bucket.total, 0);

  // Events per minute across the shown span -- honestly worth more than the
  // chart while watching live, and free to compute from what is already here.
  const perMinute = buckets.length
    ? (total / ((buckets.length * bucketSeconds) / 60)).toFixed(1)
    : "0.0";

  return (
    <div className="space-y-1 border-b border-line pb-2">
      <div className="flex items-baseline justify-between text-xs text-zinc-500">
        <span className="flex flex-wrap items-center gap-3">
          {series.map((name) => (
            <span key={name} className="flex items-center gap-1.5">
              <span
                aria-hidden="true"
                className="h-2 w-2 rounded-sm"
                style={{ backgroundColor: colors[name] || PROVENANCE_FALLBACK_COLOR }}
              />
              {name}
            </span>
          ))}
        </span>
        <span className="flex items-center gap-3 tabular-nums">
          <span className="text-zinc-300">{perMinute} events/min</span>
          <span>{widthLabel(bucketSeconds)}</span>
        </span>
      </div>

      <div className="h-24 w-full">
        {status === "loading" && !buckets.length ? (
          <div className="flex h-full items-center text-xs text-zinc-600">loading</div>
        ) : buckets.length ? (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
              <XAxis
                dataKey="bucket"
                tickFormatter={(value) => tick(value, bucketSeconds)}
                tick={{ fill: "#71717a", fontSize: 10 }}
                axisLine={{ stroke: "#2a2f36" }}
                tickLine={false}
                minTickGap={48}
              />
              <YAxis
                width={34}
                tick={{ fill: "#52525b", fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                allowDecimals={false}
              />
              <Tooltip
                contentStyle={{
                  background: "#181b20",
                  border: "1px solid #2a2f36",
                  borderRadius: 6,
                  fontSize: 12,
                }}
                labelFormatter={(value) => tick(value, bucketSeconds)}
                cursor={{ fill: "#ffffff08" }}
              />
              {series.map((name) => (
                <Bar
                  key={name}
                  dataKey={name}
                  stackId="a"
                  fill={colors[name] || PROVENANCE_FALLBACK_COLOR}
                  isAnimationActive={false}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-full items-center text-xs text-zinc-600">
            nothing in this window
          </div>
        )}
      </div>
    </div>
  );
}
