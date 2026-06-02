import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getJson } from "../api.js";

const groups = ["project", "cli", "domain"];

export default function HeatMap() {
  const [group, setGroup] = useState("project");
  const [buckets, setBuckets] = useState([]);
  const [status, setStatus] = useState("loading");

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    getJson(`/summary/heatmap?group=${group}`)
      .then((data) => {
        if (cancelled) return;
        setBuckets(data.buckets || []);
        setStatus("ready");
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [group]);

  const chartData = useMemo(
    () =>
      buckets.slice(0, 80).map((bucket) => ({
        ...bucket,
        label: `${new Date(bucket.hour).toLocaleDateString([], {
          month: "short",
          day: "numeric",
        })} ${new Date(bucket.hour).getHours()}:00`,
      })),
    [buckets],
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
        <div className="flex gap-2">
          {groups.map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setGroup(item)}
              className={[
                "focus-ring rounded-md border border-line px-3 py-2 text-sm capitalize",
                group === item ? "bg-amber-500 text-zinc-950" : "text-zinc-300 hover:bg-zinc-800",
              ].join(" ")}
            >
              {item}
            </button>
          ))}
        </div>
        <span className="text-sm text-zinc-500">{status}</span>
      </div>
      <div className="h-[520px] rounded-md border border-line bg-panel p-4">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData}>
            <CartesianGrid stroke="#2a2f36" vertical={false} />
            <XAxis dataKey="label" tick={{ fill: "#a1a1aa", fontSize: 12 }} />
            <YAxis tick={{ fill: "#a1a1aa", fontSize: 12 }} />
            <Tooltip
              contentStyle={{ background: "#181b20", border: "1px solid #2a2f36" }}
              labelStyle={{ color: "#fafafa" }}
            />
            <Bar dataKey="count" fill="#38bdf8" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
