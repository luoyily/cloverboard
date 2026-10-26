import Select from "./Select";
import { useEffect, useRef, useState } from "react";
import { DownloadSimpleIcon, XIcon } from "@phosphor-icons/react";
import { assetUrl, fetchImageNames, fetchImageTimeline, fetchImagePage, fetchAudioNames, fetchAudioTimeline, fetchAudioPage, fetchTextNames, fetchTextTimeline, fetchText, pooledResults, type ImageRecord, type AudioRecord, type TextRecord, type MediaNameSummary, type MediaTimeline, type RunSummary } from "../api";
import { runLabel } from "../comparison";
import { sampleSteps } from "../imageTimeline";
import type { WorkspaceState } from "../workspace";
import CheckControl from "./CheckControl";
import { BoundedCache } from "../dataCache";
import AudioPlayer from "./AudioPlayer";
import TextTile from "./TextTile";

type MediaKind = WorkspaceState["media_type"];
const mediaKinds = ["images", "audio", "text"] as const;
type MediaRecord = ImageRecord | AudioRecord | TextRecord;
interface MediaPage { records: MediaRecord[]; step: number | null; total: number; next_offset: number | null }
interface LoadedPage { key: string; name: string; page: MediaPage }
const catalogCache = new BoundedCache<MediaNameSummary[]>(64), timelineCache = new BoundedCache<MediaTimeline>(128), pageCache = new BoundedCache<MediaPage>(128);
function recordId(record: MediaRecord) { return "image_id" in record ? record.image_id : "audio_id" in record ? record.audio_id : `${record.name}\0${record.step}`; }
async function fetchPage(kind: MediaKind, run: string, name: string, step: number | null, limit: number, offset = 0, signal?: AbortSignal): Promise<MediaPage> {
  if (kind === "text") { const record = await fetchText(run, name, step, signal); return { records: record ? [record] : [], step: record?.step ?? null, total: record ? 1 : 0, next_offset: null }; }
  if (kind === "audio") { const page = await fetchAudioPage(run, name, step, limit, offset, signal); return { ...page, records: page.audio }; }
  const page = await fetchImagePage(run, name, step, limit, offset, signal); return { ...page, records: page.images };
}

function ImageTile({ image, run, onOpen, refreshTick }: { image: ImageRecord; run: RunSummary; onOpen: () => void; refreshTick: number }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => { setFailed(false); }, [refreshTick, run.source_online]);
  return <div className="image-tile"><button className="image-preview" onClick={onOpen} aria-label={`Open ${image.name}, ${runLabel(run)}, step ${image.step}`}>
    {failed ? <span>Image unavailable</span> : <img src={assetUrl(run.run_id, image.image_id, true)} alt={image.caption || `${image.name}, step ${image.step}`} loading="lazy" decoding="async" onError={() => setFailed(true)} />}
  </button>{image.caption && <p>{image.caption}</p>}<span className="image-dimensions">{image.width} × {image.height}</span></div>;
}

function AudioTile({ audio, run, refreshTick }: { audio: AudioRecord; run: RunSummary; refreshTick: number }) {
  return <div className="audio-tile"><AudioPlayer src={assetUrl(run.run_id, audio.audio_id)} label={`${audio.name}, ${runLabel(run)}, step ${audio.step}`} duration={audio.frames / audio.sample_rate} refreshKey={`${refreshTick}:${run.source_online}`} />
    {audio.caption && <p>{audio.caption}</p>}<div className="audio-metadata"><span>{(audio.frames / audio.sample_rate).toFixed(1)} s · {(audio.sample_rate / 1000).toLocaleString()} kHz · {audio.channels === 1 ? "Mono" : "Stereo"}</span><a href={assetUrl(run.run_id, audio.audio_id)} download aria-label={`Download ${audio.caption || audio.name}`}><DownloadSimpleIcon size={15} /></a></div>
  </div>;
}

function ImageLightbox({ image, run, onClose }: { image: ImageRecord; run: RunSummary; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  return <dialog ref={ref} className="image-lightbox" aria-label={`${image.name} image`} onCancel={onClose} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}><div className="dialog-title"><h2>{runLabel(run)} · {image.name} · Step {image.step}</h2><a className="button quiet-button" href={assetUrl(run.run_id, image.image_id)} download>Download</a><button className="button icon-button" aria-label="Close image" onClick={onClose}><XIcon size={16} /></button></div><img src={assetUrl(run.run_id, image.image_id)} alt={image.caption || image.name} />{image.caption && <p>{image.caption}</p>}</dialog>;
}

export default function MediaPanel({ workspaceId, runs, state, colors, refreshTick, onChange }: { workspaceId: string; runs: RunSummary[]; state: WorkspaceState; colors: Map<string, string>; refreshTick: number; onChange: (patch: Partial<WorkspaceState>) => void }) {
  const kind = state.media_type;
  const [catalog, setCatalog] = useState<Map<string, MediaNameSummary[]>>(new Map());
  const [timelines, setTimelines] = useState<Map<string, MediaTimeline>>(new Map());
  const [pages, setPages] = useState<Map<string, LoadedPage>>(new Map());
  const [loading, setLoading] = useState(true), [failed, setFailed] = useState(false), [pagesLoading, setPagesLoading] = useState<Set<string>>(new Set());
  const [more, setMore] = useState<Set<string>>(new Set());
  const [opened, setOpened] = useState<{ run: RunSummary; image: ImageRecord } | null>(null);
  const [stepDraft, setStepDraft] = useState<string | null>(null);
  const generation = useRef(0);
  const timelineIdentity = useRef("");
  const runKey = runs.map((run) => run.run_id).join("\0");
  const nameKey = kind === "text" ? "text_name" : kind === "audio" ? "audio_name" : "image_name";
  const stepKey = kind === "text" ? "text_step" : kind === "audio" ? "audio_step" : "image_step";
  const storedName = state[nameKey], storedStep = state[stepKey];
  const perStep = kind === "text" ? 1 : state.images_per_step;
  const [requestStep, setRequestStep] = useState(storedStep);
  function cacheKey(...parts: unknown[]) { return JSON.stringify([workspaceId, refreshTick, kind, ...parts]); }
  function changeStep(step: number | null) { setStepDraft(null); onChange({ [stepKey]: step }); }
  useEffect(() => {
    if (storedStep === requestStep) return;
    const timer = window.setTimeout(() => setRequestStep(storedStep), storedStep === null ? 0 : 150);
    return () => window.clearTimeout(timer);
  }, [storedStep, requestStep]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setFailed(false);
    setCatalog((previous) => new Map([...previous].filter(([id]) => runs.some((run) => run.run_id === id))));
    const action = kind === "text" ? fetchTextNames : kind === "audio" ? fetchAudioNames : fetchImageNames;
    void pooledResults(runs, async (run) => {
      const key = cacheKey("catalog", run.run_id), cached = catalogCache.get(key);
      if (cached) return cached;
      const result = await action(run.run_id, controller.signal);
      if (!controller.signal.aborted) catalogCache.set(key, result);
      return result;
    }, 4, (result, index) => {
      if (controller.signal.aborted) return;
      if (result.status === "fulfilled") setCatalog((previous) => new Map(previous).set(runs[index].run_id, result.value));
      else setFailed(true);
    }).then(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [runKey, kind, refreshTick]);
  const names = [...new Set(runs.flatMap((run) => (catalog.get(run.run_id) ?? []).map((item) => item.name)))].sort();
  const name = names.includes(storedName) || loading ? storedName || names[0] || "" : names[0] ?? "";
  const availableNames = storedName && !names.includes(storedName) && loading ? [storedName, ...names] : names;
  useEffect(() => { if (!storedName && name) onChange({ [nameKey]: name }); }, [storedName, name, kind]);
  const pageKey = JSON.stringify([kind, name, requestStep, perStep]);
  const requestedKey = JSON.stringify([kind, name, storedStep, perStep]);
  useEffect(() => {
    const controller = new AbortController();
    if (!name) { setTimelines(new Map()); return; }
    const identity = JSON.stringify([runKey, name, state.image_timeline_samples]);
    if (timelineIdentity.current !== identity) { timelineIdentity.current = identity; setTimelines(new Map()); }
    const action = kind === "text" ? fetchTextTimeline : kind === "audio" ? fetchAudioTimeline : fetchImageTimeline;
    void pooledResults(runs, async (run) => {
      const key = cacheKey("timeline", run.run_id, name, state.image_timeline_samples), cached = timelineCache.get(key);
      if (cached) return cached;
      const result = await action(run.run_id, name, state.image_timeline_samples, controller.signal);
      if (!controller.signal.aborted) timelineCache.set(key, result);
      return result;
    }, 4, (result, index) => {
      if (controller.signal.aborted) return;
      if (result.status === "fulfilled") setTimelines((previous) => new Map(previous).set(runs[index].run_id, result.value));
      else setFailed(true);
    });
    return () => controller.abort();
  }, [runKey, name, kind, state.image_timeline_samples, refreshTick]);
  useEffect(() => {
    const controller = new AbortController(); generation.current++; setMore(new Set());
    setPages((previous) => new Map([...previous].filter(([id, loaded]) => loaded.name === name && runs.some((run) => run.run_id === id))));
    if (!name) { setPagesLoading(new Set()); return; }
    setPagesLoading(new Set(runs.map((run) => run.run_id)));
    void pooledResults(runs, async (run) => {
      const key = cacheKey("page", run.run_id, pageKey), cached = pageCache.get(key);
      if (cached) return cached;
      const result = await fetchPage(kind, run.run_id, name, requestStep, perStep, 0, controller.signal);
      if (!controller.signal.aborted) pageCache.set(key, result);
      return result;
    }, 4, (result, index) => {
      if (controller.signal.aborted) return;
      const id = runs[index].run_id;
      if (result.status === "fulfilled") setPages((previous) => {
        const next = new Map(previous), old = previous.get(id);
        let page = result.value;
        if (old?.key === pageKey && old.page.step === page.step) {
          const records = [...new Map([...page.records, ...old.page.records].map((record) => [recordId(record), record])).values()];
          page = { ...page, records, next_offset: records.length < page.total ? records.length : null };
        }
        next.set(id, { key: pageKey, name, page });
        return next;
      });
      else setFailed(true);
      setPagesLoading((previous) => { const next = new Set(previous); next.delete(id); return next; });
    });
    return () => controller.abort();
  }, [runKey, pageKey, refreshTick]);
  async function loadMore(run: RunSummary, page: MediaPage) {
    if (page.next_offset === null) return;
    const revision = generation.current; setMore((previous) => new Set(previous).add(run.run_id));
    try {
      const next = await fetchPage(kind, run.run_id, name, page.step, perStep, page.next_offset);
      if (revision !== generation.current) return;
      setPages((previous) => {
        const result = new Map(previous), old = result.get(run.run_id);
        if (old?.key === pageKey && old.page.step === next.step) {
          const page = { ...next, records: [...new Map([...old.page.records, ...next.records].map((record) => [recordId(record), record])).values()] };
          result.set(run.run_id, { key: pageKey, name, page });
          pageCache.set(cacheKey("page", run.run_id, pageKey), page);
        }
        return result;
      });
    } catch { if (revision === generation.current) setFailed(true); }
    finally { if (revision === generation.current) setMore((previous) => { const next = new Set(previous); next.delete(run.run_id); return next; }); }
  }
  const steps = sampleSteps([...new Set(runs.flatMap((run) => timelines.get(run.run_id)?.steps ?? []))].sort((a, b) => a - b), state.image_timeline_samples);
  const selectedStep = storedStep ?? steps[steps.length - 1] ?? 0;
  const position = storedStep === null ? steps.length - 1 : Math.max(0, steps.filter((step) => step <= storedStep).length - 1);
  const label = kind === "text" ? "text" : kind === "audio" ? "audio" : "images";
  return <section className="images-panel" aria-label="Media">
    <div className="media-tabs line-tabs" role="tablist" aria-label="Media type">{mediaKinds.map((value) => <button key={value} id={`media-${value}-tab`} role="tab" aria-selected={kind === value} aria-controls="media-records" tabIndex={kind === value ? 0 : -1} onClick={() => onChange({ media_type: value })} onKeyDown={(event) => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { event.preventDefault(); const next = event.key === "Home" ? "images" : event.key === "End" ? "text" : mediaKinds[(mediaKinds.indexOf(kind) + (event.key === "ArrowLeft" ? 2 : 1)) % mediaKinds.length]; onChange({ media_type: next }); document.getElementById(`media-${next}-tab`)?.focus(); } }}>{value === "images" ? "Images" : value === "audio" ? "Audio" : "Text"}</button>)}</div>
    <div id="media-records" role="tabpanel" aria-labelledby={`media-${kind}-tab`}>
    {failed && <p className="workspace-notice" role="status">Some media could not be updated. Available records are retained; refresh to retry.</p>}
    {availableNames.length ? <><div className="image-controls"><label>Name<Select value={name} onChange={(event) => { setStepDraft(null); onChange({ [nameKey]: event.target.value }); }}>{availableNames.map((value) => <option key={value}>{value}</option>)}</Select></label><label>Step<input className="text-input image-step-input" type="number" aria-label="Media step" value={stepDraft ?? selectedStep} onBlur={() => setStepDraft(null)} onChange={(event) => { const value = event.target.value, step = Number(value); setStepDraft(value); if (value.trim() && Number.isSafeInteger(step)) changeStep(step); }} /></label><input type="range" className="smoothing-slider image-timeline" aria-label="Media timeline" min={0} max={Math.max(0, steps.length - 1)} value={Math.max(0, position)} disabled={steps.length < 2} onChange={(event) => changeStep(steps[Number(event.target.value)])} /><CheckControl checked={storedStep === null} onChange={(checked) => changeStep(checked ? null : selectedStep)}>Follow latest</CheckControl></div>
      <p className="field-help image-timeline-help">{steps.length} timeline positions · Follow latest uses each run’s latest step. Fixed steps use the nearest earlier record.</p>
      <div className="image-run-grid">{runs.map((run) => {
        const loaded = pages.get(run.run_id), page = loaded?.name === name ? loaded.page : undefined;
        const pending = pagesLoading.has(run.run_id) || storedStep !== requestStep;
        const stale = !!page && loaded?.key !== requestedKey;
        return <article className="image-run-card" key={run.run_id} aria-busy={pending}><div className="image-run-heading"><i className="swatch" style={{ background: colors.get(run.run_id) }} /><h3>{runLabel(run)}</h3><span>{page?.step != null ? `Step ${page.step}` : `No ${label}`}</span></div>
          {(pending || stale) && <p className="media-update-note" role="status">{pending ? storedStep === null ? "Updating to latest…" : `Updating to Step ${storedStep}…` : "Showing previous records. Refresh to retry."}{stale && page?.step != null ? ` Showing Step ${page.step}.` : ""}</p>}
          {page?.records.length ? <><div className={kind === "text" ? "text-samples" : kind === "audio" ? "audio-samples" : "image-samples"}>{page.records.map((record) => "image_id" in record ? <ImageTile key={record.image_id} run={run} image={record} refreshTick={refreshTick} onOpen={() => setOpened({ run, image: record })} /> : "audio_id" in record ? <AudioTile key={record.audio_id} run={run} audio={record} refreshTick={refreshTick} /> : <TextTile key={recordId(record)} record={record} />)}</div>{kind !== "text" && <div className="image-page-footer"><span>{page.records.length} of {page.total}</span>{page.next_offset !== null && <button className="button quiet-button" disabled={more.has(run.run_id) || pending || stale} onClick={() => void loadMore(run, page)}>Load more</button>}</div>}</> : <div className="image-missing">{page ? `No ${label} at or before this step.` : pending ? "Loading media…" : "Media unavailable. Refresh to retry."}</div>}
        </article>;
      })}</div></> : <div className="empty"><h2>{loading ? "Loading media…" : `No ${label} recorded`}</h2><p>Record with <code>{kind === "text" ? "log_text(name, text, step=...)" : kind === "audio" ? "log_audio(name, waveform, step=..., sample_rate=...)" : "log_image(name, image, step=...)"}</code>.</p></div>}
    </div>{opened && <ImageLightbox {...opened} onClose={() => setOpened(null)} />}
  </section>;
}
