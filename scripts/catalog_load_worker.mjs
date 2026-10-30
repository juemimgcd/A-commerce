import http from "node:http";
import fs from "node:fs";
import { performance, monitorEventLoopDelay } from "node:perf_hooks";

const [base, datasetPath, profile, rateText, durationText, output] =
  process.argv.slice(2);
const rate = Number(rateText),
  duration = Number(durationText);
const data = JSON.parse(fs.readFileSync(datasetPath, "utf8"));
const baseUrl = new URL(base);
if (
  baseUrl.hostname !== "127.0.0.1" &&
  !(process.env.BENCH_ISOLATED_BRIDGE === "1" && baseUrl.hostname === "gateway")
)
  throw new Error("Isolated target required");
const agent = new http.Agent({
  keepAlive: true,
  maxSockets: 256,
  maxFreeSockets: 256,
});
const delay = monitorEventLoopDelay({ resolution: 10 });
delay.enable();
const start =
    performance.now() +
    Math.max(0, Number(process.env.BENCH_START || 0) - Date.now()),
  cpuStart = process.cpuUsage();
const status = {},
  errors = {},
  routes = {},
  routeLatency = {},
  latencies = {},
  okLatencies = {},
  launchLag = {};
const dropped_reasons = { inflight_full: 0, scheduler_late: 0, both: 0 };
let peak_agent_queued = 0,
  peak_agent_sockets = 0,
  peak_rss_bytes = process.memoryUsage().rss;
const quotes = new Map();
const windows = Array.from(
  { length: Math.ceil(duration / 60) },
  (_, index) => ({
    seconds: Math.min(60, duration - index * 60),
    launched: 0,
    finished: 0,
    success: 0,
    dropped: 0,
    closed: false,
    sealed: false,
    dropped_reasons: { inflight_full: 0, scheduler_late: 0, both: 0 },
    status: {},
    latency_histogram_ms: {},
    route_histograms_ms: {},
  }),
);
const routeStatus = {};
const dropped_seconds = {};
let launched = 0,
  finished = 0,
  success = 0,
  active = 0,
  peak = 0,
  dropped = 0,
  bytes = 0;
const prefix = "/api/v1/customer";
function target(i) {
  const product = data.products[(i * 7919) % data.products.length];
  let kind = profile;
  if (profile === "site-mix") {
    const slot = i % 100,
      batch = Math.floor(i / 100),
      user = data.users[batch % data.users.length];
    const auth = { Authorization: `Bearer ${user.token}` };
    const bought = data.products[(batch * 7919) % data.products.length];
    if (slot < 50)
      return [
        "cache",
        `${prefix}/products/${data.products[i % 100].product_id}`,
        {},
      ];
    if (slot < 85) kind = ["search", "filter", "page"][slot % 3];
    else if (slot < 95)
      return ["private", `${prefix}/${slot % 2 ? "orders" : "cart"}`, auth];
    else if (slot < 98)
      return [
        "cart_write",
        `${prefix}/cart/items/${bought.sku_id}`,
        auth,
        "PUT",
        { quantity: 1 },
      ];
    else if (slot === 98)
      return [
        "quote",
        `${prefix}/quotes`,
        auth,
        "POST",
        {
          address_id: user.address_id,
          items: [{ sku_id: bought.sku_id, quantity: 1 }],
        },
      ];
    else
      return [
        "order",
        `${prefix}/orders`,
        {
          ...auth,
          "Idempotency-Key": `bench-${Math.floor(start)}-${process.pid}-${batch}`,
        },
        "POST",
        null,
      ];
  }
  if (profile === "read-mix") kind = ["search", "filter", "page"][i % 3];
  if (profile === "browse")
    kind = [
      "cache",
      "cache",
      "cache",
      "cache",
      "cache",
      "search",
      "filter",
      "page",
      "list",
      "list",
    ][i % 10];
  if (kind === "list") return [kind, `${prefix}/products?limit=20`, {}];
  if (kind === "cache")
    return [
      "cache",
      `${prefix}/products/${data.products[i % 100].product_id}`,
      {},
    ];
  if (profile === "private")
    return [
      "private",
      `${prefix}/${i % 2 ? "orders" : "cart"}`,
      { Authorization: `Bearer ${data.users[i % data.users.length].token}` },
    ];
  if (kind === "search")
    return [
      kind,
      `${prefix}/products?q=${encodeURIComponent("压测商品 " + (i % 100))}&limit=20`,
      {},
    ];
  if (kind === "filter")
    return [
      kind,
      `${prefix}/products?min_price=2000&max_price=3000&limit=20`,
      {},
    ];
  return [
    "page",
    `${prefix}/products?cursor=${product.product_id}&limit=20`,
    {},
  ];
}
async function send(i, due) {
  const [kind, path, headers, method = "GET", input = null] = target(
    i * Number(process.env.BENCH_STRIDE || 1) +
      Number(process.env.BENCH_OFFSET || 0),
  );
  const began = performance.now();
  record(launchLag, began - due);
  const window = windows[Math.floor(i / (rate * 60))];
  window.launched++;
  active++;
  launched++;
  peak = Math.max(peak, active);
  let done = false;
  let quoteDone = null;
  const batch = Math.floor(i / 100);
  if (kind === "quote")
    quotes.set(
      batch,
      new Promise((resolve) => {
        quoteDone = resolve;
      }),
    );
  function complete(code, error = null) {
    if (done) return;
    done = true;
    const ms = performance.now() - due;
    const bucket = Math.min(60001, Math.ceil(ms));
    window.finished++;
    window.status[code] = (window.status[code] || 0) + 1;
    window.latency_histogram_ms[bucket] =
      (window.latency_histogram_ms[bucket] || 0) + 1;
    const routeHist = (window.route_histograms_ms[kind] ??= {});
    routeHist[bucket] = (routeHist[bucket] || 0) + 1;
    if (String(code).startsWith("2")) window.success++;
    active--;
    finished++;
    status[code] = (status[code] || 0) + 1;
    routes[kind] = (routes[kind] || 0) + 1;
    const key = kind + ":" + code;
    routeStatus[key] = (routeStatus[key] || 0) + 1;
    record((routeLatency[kind] ??= {}), ms);
    record(latencies, ms);
    if (String(code).startsWith("2")) {
      success++;
      record(okLatencies, ms);
    }
    if (error) errors[error] = (errors[error] || 0) + 1;
    if (kind === "quote" && quoteDone) quoteDone(null);
    sealWindow(window, Math.floor(i / (rate * 60)));
  }
  let body = input;
  if (kind === "order") {
    const quote = quotes.has(batch) ? await quotes.get(batch) : null;
    quotes.delete(batch);
    if (!quote) {
      complete("dependency_error", "quote_unavailable");
      return;
    }
    body = { quote_id: quote };
  }
  const encoded = body === null ? null : JSON.stringify(body);
  if (encoded !== null) {
    headers["Content-Type"] = "application/json";
    headers["Content-Length"] = Buffer.byteLength(encoded);
  }
  const req = http.request(
    new URL(path, baseUrl),
    { agent, headers, method },
    (res) => {
      let errorBody = "";
      res.on("data", (chunk) => {
        bytes += chunk.length;
        if (
          (res.statusCode >= 400 || kind === "quote") &&
          errorBody.length < 10000
        )
          errorBody += chunk;
      });
      res.on("end", () => {
        let code = null;
        if (res.statusCode >= 400) {
          try {
            code =
              JSON.parse(errorBody).error?.code || `http_${res.statusCode}`;
          } catch {
            code = `http_${res.statusCode}`;
          }
        }
        if (kind === "quote" && res.statusCode === 201) {
          try {
            quoteDone(JSON.parse(errorBody).id);
          } catch {
            code = "invalid_quote";
          }
        }
        complete(String(res.statusCode), code);
      });
      res.on("error", (e) => complete("transport_error", e.code || e.name));
    },
  );
  req.setTimeout(10000, () => req.destroy(new Error("request_timeout")));
  req.on("error", (e) => complete("transport_error", e.code || e.message));
  req.end(encoded);
}
const progress = setInterval(
  () =>
    fs.writeFileSync(
      output + ".progress.json",
      JSON.stringify({
        elapsed_seconds: (performance.now() - start) / 1000,
        launched,
        finished,
        success,
        dropped,
        dropped_seconds,
        dropped_reasons,
        peak_agent_queued,
        peak_agent_sockets,
        rss_bytes: sampleMemory(),
        windows: windows.map(
          ({ latency_histogram_ms, route_histograms_ms, ...counts }) => counts,
        ),
      }),
    ),
  30000,
);
let index = 0;
const planned = Math.floor(rate * duration);
await new Promise((resolve) => {
  const timer = setInterval(() => {
    const now = performance.now();
    const should = Math.min(planned, Math.floor(((now - start) / 1000) * rate));
    while (index < should) {
      const due = start + ((index + 1) * 1000) / rate;
      if (active >= 512 || now - due > 200) {
        const window = windows[Math.floor(index / (rate * 60))];
        const reason =
          active >= 512
            ? now - due > 200
              ? "both"
              : "inflight_full"
            : "scheduler_late";
        dropped_reasons[reason]++;
        window.dropped_reasons[reason]++;
        dropped++;
        window.dropped++;
        const second = Math.floor(index / rate);
        dropped_seconds[second] = (dropped_seconds[second] || 0) + 1;
      } else send(index, due);
      index++;
      if (index % (rate * 60) === 0 || index === planned) {
        const windowIndex = Math.floor((index - 1) / (rate * 60));
        windows[windowIndex].closed = true;
        sealWindow(windows[windowIndex], windowIndex);
      }
    }
    peak_agent_queued = Math.max(
      peak_agent_queued,
      Object.values(agent.requests).reduce(
        (sum, items) => sum + items.length,
        0,
      ),
    );
    peak_agent_sockets = Math.max(
      peak_agent_sockets,
      Object.values(agent.sockets).reduce(
        (sum, items) => sum + items.length,
        0,
      ),
    );
    if (index >= planned) {
      clearInterval(timer);
      resolve();
    }
  }, 5);
});
while (active) await new Promise((r) => setTimeout(r, 20));
const wall = (performance.now() - start) / 1000;
clearInterval(progress);
delay.disable();
agent.destroy();
function record(histogram, value) {
  const bucket = Math.min(60001, Math.ceil(value));
  histogram[bucket] = (histogram[bucket] || 0) + 1;
}
function percentile(histogram) {
  const buckets = Object.entries(histogram)
    .map(([value, count]) => [Number(value), count])
    .sort((a, b) => a[0] - b[0]);
  const total = buckets.reduce((sum, [, count]) => sum + count, 0),
    result = {};
  for (const q of [50, 95, 99]) {
    let count = 0;
    result[`p${q}`] = null;
    for (const [value, frequency] of buckets) {
      count += frequency;
      if (count >= (total * q) / 100) {
        result[`p${q}`] = value > 60000 ? null : value;
        break;
      }
    }
  }
  return result;
}
function sealWindow(window, index) {
  if (!window.closed || window.sealed || window.finished !== window.launched)
    return;
  // Parent merges raw histograms from disk, never averages child percentiles.
  fs.writeFileSync(output + `.window${index}.json`, JSON.stringify(window));
  window.latency_ms = percentile(window.latency_histogram_ms);
  window.route_latency_ms = Object.fromEntries(
    Object.entries(window.route_histograms_ms).map(([route, hist]) => [
      route,
      percentile(hist),
    ]),
  );
  delete window.latency_histogram_ms;
  delete window.route_histograms_ms;
  window.sealed = true;
}
function sampleMemory() {
  const rss = process.memoryUsage().rss;
  peak_rss_bytes = Math.max(peak_rss_bytes, rss);
  return rss;
}
sampleMemory();
const cpu = process.cpuUsage(cpuStart);
const report = {
  dropped_seconds,
  dropped_reasons,
  windows,
  peak_agent_queued,
  peak_agent_sockets,
  generator_peak_rss_bytes: peak_rss_bytes,
  histogram_limit_ms: 60000,
  histogram_overflow: latencies[60001] || 0,
  latency_histogram_ms: latencies,
  route_histograms_ms: routeLatency,
  profile,
  target_rps: rate,
  duration_seconds: duration,
  wall_seconds: wall,
  planned,
  launched,
  finished,
  success,
  dropped,
  completed_rps: finished / wall,
  successful_rps: success / wall,
  success_fraction_of_planned: success / planned,
  failure_fraction: (planned - success) / planned,
  status,
  errors,
  routes,
  route_status: routeStatus,
  latency_ms: percentile(latencies),
  successful_latency_ms: percentile(okLatencies),
  route_latency_ms: Object.fromEntries(
    Object.entries(routeLatency).map(([k, v]) => [k, percentile(v)]),
  ),
  launch_lag_ms: percentile(launchLag),
  peak_in_flight: peak,
  response_bytes: bytes,
  generator_cpu_percent: ((cpu.user + cpu.system) / 1e6 / wall) * 100,
  generator_event_loop_delay_p99_ms: delay.percentile(99) / 1e6,
  generator_event_loop_delay_max_ms: delay.max / 1e6,
};
fs.writeFileSync(output, JSON.stringify(report, null, 2) + "\n");
const {
  windows: minuteWindows,
  latency_histogram_ms,
  route_histograms_ms,
  ...summary
} = report;
console.log(JSON.stringify(summary));
