import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const [base, dataset, profile, rateText, duration, output, workersText = "4"] =
  process.argv.slice(2);
const workers = Number(workersText),
  rate = Number(rateText),
  start = Date.now() + 2000;
if (
  !Number.isSafeInteger(workers) ||
  workers < 1 ||
  workers > 32 ||
  !Number.isSafeInteger(rate) ||
  rate <= 0 ||
  rate % workers ||
  !Number.isFinite(Number(duration)) ||
  Number(duration) <= 0
)
  throw new Error(
    "Use a positive integer RPS divisible by 1–32 workers and a positive duration",
  );
const workerPath = fileURLToPath(
  new URL("./catalog_load_worker.mjs", import.meta.url),
);
const promises = [];
const children = [];
function stopChildren() {
  for (const child of children) {
    if (child.exitCode === null && child.signalCode === null)
      child.kill("SIGTERM");
  }
}
process.on("SIGINT", stopChildren);
process.on("SIGTERM", stopChildren);
for (let index = 0; index < workers; index++) {
  promises.push(
    new Promise((resolve, reject) => {
      const child = spawn(
        process.execPath,
        [
          workerPath,
          base,
          dataset,
          profile,
          String(rate / workers),
          duration,
          output + `.part${index}.json`,
        ],
        {
          env: {
            ...process.env,
            BENCH_STRIDE: String(workers),
            BENCH_OFFSET: String(index),
            BENCH_START: String(start),
          },
          stdio: ["ignore", "ignore", "inherit"],
        },
      );
      children.push(child);
      child.on("error", reject);
      child.on("exit", (code) =>
        code === 0
          ? resolve()
          : reject(new Error(`generator ${index} exited ${code}`)),
      );
    }),
  );
}
try {
  await Promise.all(promises);
} catch (error) {
  stopChildren();
  await Promise.allSettled(promises);
  throw error;
} finally {
  process.off("SIGINT", stopChildren);
  process.off("SIGTERM", stopChildren);
}
const parts = Array.from({ length: workers }, (_, i) =>
  JSON.parse(fs.readFileSync(output + `.part${i}.json`, "utf8")),
);
const merged = {
  profile,
  target_rps: rate,
  duration_seconds: Number(duration),
  generator_processes: workers,
  wall_seconds: Math.max(...parts.map((p) => p.wall_seconds)),
};
for (const key of [
  "planned",
  "launched",
  "finished",
  "success",
  "dropped",
  "response_bytes",
])
  merged[key] = parts.reduce((sum, p) => sum + p[key], 0);
for (const key of [
  "status",
  "errors",
  "routes",
  "route_status",
  "latency_histogram_ms",
  "dropped_seconds",
  "dropped_reasons",
]) {
  merged[key] = {};
  for (const part of parts)
    for (const [label, count] of Object.entries(part[key]))
      merged[key][label] = (merged[key][label] || 0) + count;
}
function percentile(histogram) {
  const buckets = Object.entries(histogram)
    .map(([x, n]) => [Number(x), n])
    .sort((a, b) => a[0] - b[0]);
  const total = buckets.reduce((sum, [x, n]) => sum + n, 0),
    result = {};
  for (const q of [50, 95, 99]) {
    let count = 0;
    result[`p${q}`] = null;
    for (const [value, n] of buckets) {
      count += n;
      if (count >= (total * q) / 100) {
        result[`p${q}`] = value > 60000 ? null : value;
        break;
      }
    }
  }
  return result;
}
merged.latency_ms = percentile(merged.latency_histogram_ms);
merged.route_latency_ms = {};
for (const route of Object.keys(merged.routes)) {
  const histogram = {};
  for (const part of parts)
    for (const [bucket, count] of Object.entries(
      part.route_histograms_ms[route] || {},
    ))
      histogram[bucket] = (histogram[bucket] || 0) + count;
  merged.route_latency_ms[route] = percentile(histogram);
}
merged.windows = parts[0].windows.map((first, index) => {
  const result = {
    seconds: first.seconds,
    planned: rate * first.seconds,
    launched: 0,
    success: 0,
    dropped: 0,
    status: {},
    dropped_reasons: {},
    latency_histogram_ms: {},
    route_histograms_ms: {},
  };
  for (let partIndex = 0; partIndex < workers; partIndex++) {
    const current = JSON.parse(
      fs.readFileSync(
        output + `.part${partIndex}.json.window${index}.json`,
        "utf8",
      ),
    );
    for (const key of ["launched", "success", "dropped"])
      result[key] += current[key];
    for (const key of ["status", "latency_histogram_ms", "dropped_reasons"])
      for (const [label, count] of Object.entries(current[key]))
        result[key][label] = (result[key][label] || 0) + count;
    for (const [route, hist] of Object.entries(current.route_histograms_ms)) {
      const target = (result.route_histograms_ms[route] ??= {});
      for (const [label, count] of Object.entries(hist))
        target[label] = (target[label] || 0) + count;
    }
  }
  const { latency_histogram_ms, route_histograms_ms, ...summary } = result;
  return {
    ...summary,
    failure_fraction: (result.planned - result.success) / result.planned,
    latency_ms: percentile(latency_histogram_ms),
    route_latency_ms: Object.fromEntries(
      Object.entries(route_histograms_ms).map(([route, hist]) => [
        route,
        percentile(hist),
      ]),
    ),
  };
});
merged.histogram_limit_ms = 60000;
merged.histogram_overflow = parts.reduce(
  (sum, p) => sum + p.histogram_overflow,
  0,
);
for (const key of [
  "peak_agent_queued",
  "peak_agent_sockets",
  "peak_in_flight",
  "generator_peak_rss_bytes",
  "generator_event_loop_delay_max_ms",
])
  merged[key] = parts.map((p) => p[key]);
merged.successful_rps = merged.success / merged.wall_seconds;
merged.failure_fraction = (merged.planned - merged.success) / merged.planned;
merged.generator_cpu_percent = parts.map((p) => p.generator_cpu_percent);
merged.generator_event_loop_delay_p99_ms = parts.map(
  (p) => p.generator_event_loop_delay_p99_ms,
);
merged.launch_lag_ms = parts.map((p) => p.launch_lag_ms);
fs.writeFileSync(output, JSON.stringify(merged, null, 2) + "\n");
const { latency_histogram_ms, ...summary } = merged;
console.log(JSON.stringify(summary));
