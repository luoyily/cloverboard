(() => {
  const config = __PERF_CONFIG__;
  const start = performance.now(), requests = [], tasks = [], errors = [];
  let active = 0, done = false, firstCurve = null, firstCurveDom = null, finishing = false;
  const elapsed = () => +(performance.now() - start).toFixed(2);
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    const method = (init.method || "GET").toUpperCase();
    if (url.origin === location.origin && url.pathname.startsWith("/api/") && !["GET", "HEAD"].includes(method)) {
      return new Response(JSON.stringify({ detail: "Performance probe is read-only" }), { status: 403, headers: { "Content-Type": "application/json" } });
    }
    const headers = new Headers(init.headers);
    headers.set("X-Cloverboard-Perf-Case", config.case);
    headers.set("X-Cloverboard-Perf-Round", String(config.round));
    const row = { route: url.pathname, run_id: url.searchParams.get("run_id"), metric: url.searchParams.get("metric"), metrics: url.searchParams.getAll("metrics"), start_ms: elapsed() };
    requests.push(row); active++;
    try {
      const response = await nativeFetch(input, { ...init, headers });
      row.headers_ms = elapsed(); row.status = response.status;
      const json = response.json.bind(response);
      response.json = async () => {
        try { return await json(); }
        finally { row.end_ms = elapsed(); row.duration_ms = +(row.end_ms - row.start_ms).toFixed(2); active--; }
      };
      return response;
    } catch (error) {
      row.error = error.name; row.end_ms = elapsed(); active--; throw error;
    }
  };
  window.addEventListener("error", (event) => errors.push(String(event.message).slice(0, 500)));
  window.addEventListener("unhandledrejection", (event) => errors.push(String(event.reason).slice(0, 500)));
  let observer;
  try { observer = new PerformanceObserver((list) => { for (const entry of list.getEntries()) tasks.push({ start_ms: +(entry.startTime - start).toFixed(2), duration_ms: +entry.duration.toFixed(2) }); }); observer.observe({ type: "longtask", buffered: true }); } catch {}
  function finish(error) {
    if (done) return;
    done = true; clearTimeout(timeout); observer?.disconnect(); mutations.disconnect();
    const series = requests.filter((row) => ["/api/scalars", "/api/scalars-batch"].includes(row.route) && row.end_ms != null);
    if (!error && requests.some((row) => row.error || row.status >= 400)) error = "Some data requests failed; inspect the request timeline";
    if (!error && errors.length) error = "Browser errors occurred; inspect the error list";
    const lastSeries = series.length ? Math.max(...series.map((row) => row.end_ms)) : null;
    const stages = {};
    for (const route of ["/api/runs", "/api/workspace", "/api/metrics", "/api/scalars", "/api/scalars-batch", "/api/scalar-summary"]) {
      const rows = requests.filter((row) => row.route === route && row.end_ms != null);
      if (rows.length) stages[route] = { count: rows.length, start_ms: Math.min(...rows.map((row) => row.start_ms)), end_ms: Math.max(...rows.map((row) => row.end_ms)) };
    }
    const result = { ...config, error, first_curve_dom_ms: firstCurveDom, first_curve_paint_ms: firstCurve, last_series_json_ms: lastSeries,
      paint_delay_ms: firstCurve !== null && firstCurveDom !== null ? +(firstCurve - firstCurveDom).toFixed(2) : null, document_visibility: document.visibilityState,
      post_data_paint_ms: firstCurve != null && lastSeries != null ? +(firstCurve - lastSeries).toFixed(2) : null,
      chart_count: document.querySelectorAll(".chart-card").length, requests, stages, long_tasks: tasks,
      long_task_total_ms: +tasks.reduce((sum, task) => sum + task.duration_ms, 0).toFixed(2), errors,
      resources: performance.getEntriesByType("resource").map((item) => ({ route: new URL(item.name).pathname, start_ms: +(item.startTime - start).toFixed(2), duration_ms: +item.duration.toFixed(2), transfer_bytes: item.transferSize, decoded_bytes: item.decodedBodySize })) };
    parent.postMessage({ cloverboardPerformance: result }, location.origin);
  }
  function detect() {
    if (done || finishing || !document.querySelector('.interactive-chart .series-path[d]:not([d=""])')) return;
    finishing = true;
    firstCurveDom = elapsed();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      firstCurve = elapsed();
      // Progressive catalogs can schedule another fetch in the next React
      // effect. Require a short quiet window, preserving the first-paint clock.
      let idleSince = null;
      const settle = () => {
        if (active === 0 && !document.querySelector('.chart-card [role="status"]')) {
          idleSince ??= performance.now();
          if (performance.now() - idleSince >= 100) { finish(null); return; }
        } else idleSince = null;
        setTimeout(settle, 50);
      };
      settle();
    }));
  }
  const mutations = new MutationObserver(detect);
  mutations.observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ["d"] });
  const timeout = setTimeout(() => finish("Charts did not become ready within 40 seconds"), 40000);
})();
