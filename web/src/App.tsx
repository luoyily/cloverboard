import { lazy, Suspense, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { fetchMetrics, fetchRuns, fetchSeriesBatch, fetchSummary, fetchSources, pooledResults, seriesKey, type RunSummary, type ScalarPoint, type ScalarSummary, type SourceStatus, type MetricField } from "./api";
import { RequestCache } from "./dataCache";
import { applyTheme, loadThemeMode, nextThemeMode, resolveTheme, saveThemeMode, type ResolvedTheme, type ThemeMode } from "./theme";
import { defaultMetricColumns, flattenConfig, parameterColumns } from "./comparison";
import { useWorkspace, type WorkspaceState } from "./workspace";
import { topRunIds } from "./features";
import { recentRunIds, replaceSelection, selectionPatch, sortRuns } from "./runSelection";
import { useColors } from "./useColors";
import ColorMenu from "./components/ColorMenu";
import ColorLegend from "./components/ColorLegend";
import RunAppearance from "./components/RunAppearance";
import ChartGrid from "./components/ChartGrid";
import CompareControls from "./components/CompareControls";
import CompareTable from "./components/CompareTable";
import RunList from "./components/RunList";
import TopBar from "./components/TopBar";
import MediaPanel from "./components/MediaPanel";
import TagsDialog from "./components/TagsDialog";
const RunDetailsDialog = lazy(() => import("./components/RunDetailsDialog"));

export default function App() {
  const workspace = useWorkspace();
  const { state, update, ready } = workspace;
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [sources, setSources] = useState<SourceStatus[]>([]);
  const [detailsRun, setDetailsRun] = useState<{ run: RunSummary; tab: "overview" | "notes" } | null>(null);
  const catalogCache = useRef(new RequestCache<MetricField[]>(128));
  const seriesCache = useRef(new RequestCache<ScalarPoint[]>(128));
  const summaryCache = useRef(new RequestCache<ScalarSummary>(256));
  const [catalogs, setCatalogs] = useState(new Map<string, { tick: number; fields: MetricField[] }>());
  const [series, setSeries] = useState<Map<string, ScalarPoint[]>>(() => new Map());
  const [summaries, setSummaries] = useState<Map<string, ScalarSummary>>(() => new Map());
  const [loadedSummary, setLoadedSummary] = useState("");
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [locateRequest, setLocateRequest] = useState<{ id: string; sequence: number } | null>(null);
  useEffect(() => { setLocateRequest(null); }, [state.search, state.search_regex, state.tag_filter, state.tag_filter_match]);
  const [appearance, setAppearance] = useState<RunSummary | null>(null);
  const [tagRuns, setTagRuns] = useState<RunSummary[] | null>(null);
  const [baselineUndo, setBaselineUndo] = useState<string | null>(null);
  const [themeMode, setThemeMode] = useState<ThemeMode>(loadThemeMode);
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() => resolveTheme(loadThemeMode()));
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [refreshTick, setRefreshTick] = useState(0);
  const [dataError, setDataError] = useState(false);
  const [discovering, setDiscovering] = useState(true);
  const [seriesProgress, setSeriesProgress] = useState({ key: "", pending: new Set<string>() });
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    try { const value = Number(localStorage.getItem("cloverboard:sidebar-width")); return Math.max(220, Math.min(value >= 220 && value <= 600 ? value : 280, window.innerWidth * .45)); } catch { return 280; }
  });
  useEffect(() => { try { localStorage.setItem("cloverboard:sidebar-width", String(sidebarWidth)); } catch { /* Optional browser preference. */ } }, [sidebarWidth]);
  useEffect(() => {
    const constrain = () => setSidebarWidth((value) => Math.max(220, Math.min(value, window.innerWidth * .45)));
    window.addEventListener("resize", constrain);
    return () => window.removeEventListener("resize", constrain);
  }, []);
  const orderedRuns = useMemo(() => sortRuns(runs, state.run_sort), [runs, state.run_sort]);

  const selectedIds = useMemo(() => {
    const ids = new Set(state.selected_ids ?? recentRunIds(runs));
    if (state.baseline_id) ids.add(state.baseline_id);
    return ids;
  }, [state.selected_ids, state.baseline_id, runs]);
  const selectedRuns = useMemo(() => orderedRuns.filter((run) => selectedIds.has(run.run_id)), [orderedRuns, selectedIds]);
  const selectionKey = selectedRuns.map((run) => run.run_id).sort().join("\u0000");
  const metricFields = useMemo(() => [...new Map(selectedRuns.flatMap((run) => catalogs.get(run.run_id)?.fields ?? []).map((field) => [field.metric, field])).values()].sort((a, b) => a.metric.localeCompare(b.metric)), [selectionKey, catalogs]);
  const metrics = useMemo(() => metricFields.map((field) => field.metric).sort(), [metricFields]);
  const catalogReady = selectedRuns.every((run) => catalogs.get(run.run_id)?.tick === refreshTick);
  const needsCatalog = state.panel !== "images" || state.colors.top_k.enabled;
  const needsSeries = state.panel === "charts";
  const filteredMetricFields = metricFields.filter((field) => field.metric.toLowerCase().includes(state.metric_filter.trim().toLowerCase()));
  const visibleMetricNames = new Set(filteredMetricFields.map((field) => field.metric));
  const seriesRequests = selectedRuns.flatMap((run) => (catalogs.get(run.run_id)?.fields ?? []).filter((field) => !state.collapsed_groups.includes(field.group) && visibleMetricNames.has(field.metric)).map(({ metric }) => ({ run, metric })));
  const seriesRequestKey = JSON.stringify([seriesRequests.map(({ run, metric }) => [run.run_id, metric]), state.smoothing, refreshTick]);
  const loadingMetrics = new Set(seriesRequests.filter(({ run, metric }) => seriesProgress.key !== seriesRequestKey || seriesProgress.pending.has(seriesKey(run.run_id, metric))).map(({ metric }) => metric));
  const chosenMetrics = useMemo(() => (state.metric_columns ?? defaultMetricColumns(metrics)).filter((name) => metrics.includes(name)), [state.metric_columns, metrics]);
  const summaryMetrics = state.panel === "table" ? [...chosenMetrics] : state.panel === "charts" ? metrics.filter((metric) => state.targets[metric]) : [];
  if (state.colors.top_k.enabled && state.colors.top_k.metric && !summaryMetrics.includes(state.colors.top_k.metric)) summaryMetrics.push(state.colors.top_k.metric);
  const summaryKey = JSON.stringify([selectionKey, summaryMetrics, summaryMetrics.map((metric) => state.targets[metric] ?? null), refreshTick]);

  useEffect(() => {
    applyTheme(themeMode);
    saveThemeMode(themeMode);
    setResolvedTheme(resolveTheme(themeMode));
    if (themeMode !== "system") return;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setResolvedTheme(resolveTheme("system"));
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [themeMode]);

  useEffect(() => {
    let cancelled = false;
    setDiscovering(true);
    let aggregateLoaded = false;
    const publish = (nextRuns: RunSummary[]) => {
      if (cancelled) return;
      setRuns([...nextRuns].sort((a, b) => b.started_at - a.started_at));
      setStatus("ready");
    };
    async function loadRuns() {
      try {
        const configured = await fetchSources().catch(() => null);
        if (cancelled) return;
        if (configured) setSources(configured);
        // Local-only installations need one scan. With peers, publish local
        // discoveries independently so a slow source cannot delay their charts.
        if (configured === null || configured.some((source) => source.kind === "peer")) {
          void fetchRuns(true).then((localRuns) => {
            if (cancelled || aggregateLoaded) return;
            setRuns((previous) => [...localRuns, ...previous.filter((run) => !localRuns.some((local) => local.run_id === run.run_id) && run.source !== "local")].sort((a, b) => b.started_at - a.started_at));
            setStatus("ready");
          }).catch(() => {});
        }
        const nextRuns = await fetchRuns();
        if (cancelled) return;
        aggregateLoaded = true;
        publish(nextRuns);
        setDiscovering(false);
        if (configured === null || configured.some((source) => source.kind === "peer")) {
          const sourceStatus = await fetchSources().catch(() => configured ?? []);
          if (!cancelled) setSources(sourceStatus);
        }
      } catch {
        if (!cancelled) { setDiscovering(false); setStatus("error"); }
      }
    }
    void loadRuns();
    return () => { cancelled = true; };
  }, [refreshTick]);

  useEffect(() => {
    if (!ready || state.refresh_interval === 0) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) setRefreshTick((tick) => tick + 1);
    }, state.refresh_interval * 1000);
    return () => window.clearInterval(timer);
  }, [ready, state.refresh_interval]);

  useEffect(() => {
    if (ready && !discovering && state.selected_ids === null && runs.some((run) => run.has_scalars)) {
      update({ selected_ids: recentRunIds(runs) });
    }
  }, [ready, discovering, state.selected_ids, runs]);

  useEffect(() => {
    if (!ready || !needsCatalog) return;
    let cancelled = false;
    const controller = new AbortController();
    setDataError(false);
    void pooledResults(selectedRuns, (run) => catalogCache.current.load(JSON.stringify([run.run_id, refreshTick]), async () => (await fetchMetrics(run.run_id)).fields), 4, (result, index) => {
      if (cancelled) return;
      const id = selectedRuns[index].run_id;
      setCatalogs((previous) => new Map(previous).set(id, { tick: refreshTick, fields: result.status === "fulfilled" ? result.value : previous.get(id)?.fields ?? [] }));
      if (result.status === "rejected") setDataError(true);
    }, controller.signal);
    return () => { cancelled = true; controller.abort(); };
  }, [ready, needsCatalog, selectionKey, refreshTick]);

  useEffect(() => {
    if (!ready || !needsSeries) return;
    let cancelled = false;
    const controller = new AbortController();
    setSeriesProgress({ key: seriesRequestKey, pending: new Set(seriesRequests.map(({ run, metric }) => seriesKey(run.run_id, metric))) });
    setSeries((previous) => new Map([...previous].filter(([key]) => selectedRuns.some((run) => key.startsWith(run.run_id + "\u0000")))));
    const loads = new Map<string, Promise<ScalarPoint[]>>();
    for (const run of selectedRuns) {
      const metrics = seriesRequests.filter((item) => item.run.run_id === run.run_id).map((item) => item.metric);
      for (let start = 0; start < metrics.length; start += 16) {
        const batch = metrics.slice(start, start + 16);
        const keyed = new Map(batch.map((metric) => [JSON.stringify([run.run_id, metric, state.smoothing, refreshTick]), metric]));
        const keys = [...keyed.keys()];
        const promises = seriesCache.current.loadMany(keys, async (missing) => {
          const records = await fetchSeriesBatch(run.run_id, missing.map((key) => keyed.get(key)!), state.smoothing);
          const points = new Map(records.map((record) => [record.metric, record.points]));
          return missing.map((key) => { const value = points.get(keyed.get(key)!); if (!value) throw Error("Incomplete series batch."); return value; });
        });
        // A cancelled progressive subscriber may stop consuming later entries.
        void Promise.allSettled(promises);
        batch.forEach((metric, index) => loads.set(seriesKey(run.run_id, metric), promises[index]));
      }
    }
    void pooledResults(seriesRequests, async ({ run, metric }) => {
      const points = await loads.get(seriesKey(run.run_id, metric))!;
      return { key: seriesKey(run.run_id, metric), points };
    }, 4, (result, index) => {
      if (cancelled) return;
      if (result.status === "fulfilled") setSeries((previous) => new Map(previous).set(result.value.key, result.value.points));
      else setDataError(true);
      const { run, metric } = seriesRequests[index];
      setSeriesProgress((previous) => { const pending = new Set(previous.pending); pending.delete(seriesKey(run.run_id, metric)); return { key: seriesRequestKey, pending }; });
    }, controller.signal);
    return () => { cancelled = true; controller.abort(); };
  }, [ready, needsSeries, seriesRequestKey]);

  useEffect(() => {
    if (!ready || !needsCatalog) return;
    let cancelled = false;
    const controller = new AbortController();
    const requests = selectedRuns.flatMap((run) => summaryMetrics.map((metric) => ({ run, metric })));
    void pooledResults(requests, async ({ run, metric }) => {
      const target = state.targets[metric], cacheKey = JSON.stringify([run.run_id, metric, target ?? null, refreshTick]);
      const summary = await summaryCache.current.load(cacheKey, () => fetchSummary(run.run_id, metric, target));
      return [seriesKey(run.run_id, metric), summary] as const;
    }, 4, undefined, controller.signal)
      .then((pairs) => { if (!cancelled) { setSummaries(new Map(pairs.filter((pair) => pair.status === "fulfilled").map((pair) => pair.value))); setLoadedSummary(summaryKey); if (pairs.some((pair) => pair.status === "rejected")) setDataError(true); } })
      .catch(() => { if (!cancelled) { setDataError(true); setSummaries(new Map()); } });
    return () => { cancelled = true; controller.abort(); };
  }, [ready, needsCatalog, summaryKey, refreshTick]);

  const { colors, registry, localOnly: colorsLocalOnly } = useColors(runs, state.colors, resolvedTheme, workspace.workspaceId, workspace.writable, refreshTick);
  const visibleSummaries = loadedSummary === summaryKey ? summaries : new Map<string, ScalarSummary>();
  const topIds = useMemo(() => loadedSummary === summaryKey ? topRunIds(selectedRuns, summaries, state.colors.top_k) : null, [selectedRuns, summaries, state.colors.top_k, loadedSummary, summaryKey]);
  const configs = useMemo(() => new Map(selectedRuns.map((run) => [run.run_id, flattenConfig(run.config)])), [selectedRuns]);
  const params = useMemo(() => parameterColumns([...configs.values()]), [configs]);

  function toggleRun(runId: string) {
    bulkSelect([runId], !selectedIds.has(runId));
  }

  function bulkSelect(ids: string[], selected: boolean) {
    const patch = selectionPatch(state, selectedIds, ids, selected);
    if (state.baseline_id && !patch.baseline_id) {
      setBaselineUndo(state.baseline_id);
    }
    update(patch);
  }

  function selectOnly(ids: string[]) {
    const patch = replaceSelection(state, ids);
    if (state.baseline_id && !patch.baseline_id) setBaselineUndo(state.baseline_id);
    update(patch);
  }

  function setBaseline(id: string | null) {
    setBaselineUndo(null);
    update({ baseline_id: id, ...(id ? { selected_ids: [...new Set([...selectedIds, id])] } : {}) });
  }

  function openDetails(run: RunSummary, tab: "overview" | "notes") {
    setDetailsRun({ run, tab });
  }

  function tagsSaved(id: string, tags: string[]) {
    setRuns((previous) => previous.map((run) => run.run_id === id ? { ...run, tags } : run));
    setDetailsRun((previous) => previous?.run.run_id === id ? { ...previous, run: { ...previous.run, tags } } : previous);
  }

  let content;
  if (workspace.loadError || status === "error") {
    content = <div className="empty" role="alert"><h2>Unable to connect</h2><p>Make sure <code>cloverboard serve</code> is running, then refresh.</p></div>;
  } else if (!ready || status === "loading") {
    content = <div className="empty" role="status">Loading runs…</div>;
  } else if (discovering && (runs.length === 0 || selectedRuns.length === 0)) {
    content = <div className="empty" role="status">Loading runs…</div>;
  } else if (runs.length === 0) {
    content = <div className="empty"><h2>No runs yet</h2><p>Log your first run with the SDK, then start <code>cloverboard serve --logdir ./runs</code>.</p></div>;
  } else if (selectedRuns.length === 0) {
    content = <div className="empty">Select a run to view its metrics.</div>;
  } else if (state.panel === "images") {
    content = <MediaPanel key={state.media_type} workspaceId={workspace.workspaceId} runs={selectedRuns} state={state} colors={colors} refreshTick={refreshTick} onChange={update} />;
  } else if (state.panel === "table") {
    content = <CompareTable runs={selectedRuns} configs={configs} params={params} metrics={chosenMetrics} summaries={visibleSummaries} colors={colors} state={state}
      loading={(!metrics.length && !catalogReady) || loadedSummary !== summaryKey} onChange={update} focusedId={focusedId} onFocusRun={setFocusedId} onAppearance={setAppearance} onDetails={openDetails} onBaseline={setBaseline} topIds={topIds} onEditTags={setTagRuns} />;
  } else if (!metricFields.length && !catalogReady) {
    content = <div className="empty" role="status">Loading metrics…</div>;
  } else {
    content = <ChartGrid metrics={filteredMetricFields} loadingMetrics={loadingMetrics} refreshTick={refreshTick} runs={selectedRuns} series={series} colors={colors} smoothing={state.smoothing} state={state} summaries={visibleSummaries} onChange={update} focusedId={focusedId} onFocusRun={setFocusedId} onLocateRun={(id) => setLocateRequest((previous) => ({ id, sequence: (previous?.sequence ?? 0) + 1 }))} topIds={topIds} />;
  }

  return <div className="app" style={{ "--sidebar-width": `${sidebarWidth}px` } as CSSProperties}>
    <RunList runs={orderedRuns} selectedIds={selectedIds} colors={colors} onToggle={toggleRun} onBulkSelect={bulkSelect} onSelectOnly={selectOnly} sidebarWidth={sidebarWidth} onSidebarWidth={setSidebarWidth} filter={state.search} onFilterChange={(search) => update({ search })}
      state={state} onChange={update} onEditTags={setTagRuns} locateRequest={locateRequest}
      baselineId={state.baseline_id} focusedId={focusedId} onFocusRun={setFocusedId} onAppearance={setAppearance} onDetails={openDetails} onBaseline={setBaseline} />
    <div className="workspace">
      <TopBar themeMode={themeMode} onThemeToggle={() => setThemeMode((mode) => nextThemeMode(mode))}
        onRefresh={() => { setRefreshTick((tick) => tick + 1); if (workspace.loadError) workspace.retry(); }}
        state={state} views={workspace.views} writable={workspace.writable} ready={ready} localOnly={workspace.localOnly}
        onChange={update} onOpenView={workspace.openView} onSaveView={workspace.saveView} onDeleteView={workspace.deleteView} onRenameView={workspace.renameView}
        metrics={metrics}
        colorControls={<ColorMenu runs={runs} selectedIds={selectedIds} settings={state.colors} registry={registry} localOnly={colorsLocalOnly} onChange={(colors) => update({ colors })} />}
        compareControls={<CompareControls state={state} metrics={metrics} chosenMetrics={chosenMetrics} params={params} onChange={update} />} />
      <main className="content" id="workspace-panel" role="tabpanel" aria-labelledby={`${state.panel}-tab`} tabIndex={0}>
        {workspace.linkNotice && <p className="workspace-notice" role="status">{workspace.linkNotice}</p>}
        {state.selected_ids?.some((id) => !runs.some((run) => run.run_id === id)) && status === "ready" && !discovering && <p className="workspace-notice" role="status">Some runs in this selection are unavailable on this instance.</p>}
        {sources.some((source) => !source.online) && <p className="workspace-notice" role="status">Offline: {sources.filter((source) => !source.online).map((source) => source.label).join(", ")}. Selection and colors are retained.</p>}
        {dataError && state.panel !== "images" && <p className="data-error" role="alert">Some metrics could not be updated. Available curves and previously loaded samples are retained.</p>}
        {baselineUndo && <p className="workspace-notice" role="status">Baseline cleared.<button className="text-button" onClick={() => setBaseline(baselineUndo)}>Undo</button><button className="text-button" onClick={() => setBaselineUndo(null)} aria-label="Dismiss baseline notice">×</button></p>}
        <ColorLegend runs={selectedRuns} settings={state.colors} colors={colors} theme={resolvedTheme} />
        {content}
      </main>
    </div>
    {appearance && <RunAppearance key={appearance.run_id} run={appearance} runs={runs} selectedIds={selectedIds} settings={state.colors} registry={registry} colors={colors} theme={resolvedTheme} onChange={(colors) => update({ colors })} onClose={() => { setAppearance(null); setFocusedId(null); }} />}
    {detailsRun && <Suspense fallback={<div className="details-loading" role="status">Loading details…</div>}><RunDetailsDialog key={detailsRun.run.run_id} run={detailsRun.run} initialTab={detailsRun.tab} onEditTags={setTagRuns} onNoteSaved={() => setRefreshTick((tick) => tick + 1)} onClose={() => { setDetailsRun(null); setFocusedId(null); }} /></Suspense>}
    {tagRuns && <TagsDialog runs={tagRuns} onSaved={tagsSaved} onClose={() => setTagRuns(null)} />}
  </div>;
}
