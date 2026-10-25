import { useEffect, useRef, useState, type CSSProperties } from "react";
import { PauseIcon, PlayIcon, SpeakerHighIcon, SpeakerSlashIcon } from "@phosphor-icons/react";

function audioTime(seconds: number) {
  const value = Math.max(0, Math.floor(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}

export default function AudioPlayer({ src, label, duration, refreshKey }: { src: string; label: string; duration: number; refreshKey: string }) {
  const audio = useRef<HTMLAudioElement>(null), pendingSeek = useRef<number | null>(null);
  const [playing, setPlaying] = useState(false), [muted, setMuted] = useState(false), [failed, setFailed] = useState(false);
  const [position, setPosition] = useState(0), [length, setLength] = useState(duration);
  useEffect(() => { if (failed) { setFailed(false); audio.current?.load(); } }, [refreshKey]);
  function readMetadata() {
    const element = audio.current;
    if (!element) return;
    if (Number.isFinite(element.duration) && element.duration > 0) setLength(element.duration);
    if (pendingSeek.current !== null) { element.currentTime = Math.min(pendingSeek.current, element.duration || length); pendingSeek.current = null; }
  }
  function seek(value: number) {
    const element = audio.current;
    if (!element) return;
    setPosition(value);
    if (element.readyState >= 1) element.currentTime = value;
    else {
      pendingSeek.current = value;
      // Metadata stays lazy until the user plays or seeks.
      if (element.networkState !== HTMLMediaElement.NETWORK_LOADING) { element.preload = "metadata"; element.load(); }
    }
  }
  async function togglePlayback() {
    const element = audio.current;
    if (!element) return;
    if (!element.paused) { element.pause(); return; }
    try { await element.play(); }
    catch { if (element.error) setFailed(true); }
  }
  return <div>
    <audio ref={audio} preload="none" src={src} onLoadedMetadata={readMetadata} onDurationChange={readMetadata} onTimeUpdate={() => setPosition(audio.current?.currentTime ?? 0)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => { setFailed(true); setPlaying(false); }} />
    <div className="audio-player" role="group" aria-label={label}>
      <button className="button icon-button" type="button" disabled={failed} aria-label={`${playing ? "Pause" : "Play"} ${label}`} title={playing ? "Pause" : "Play"} onClick={() => void togglePlayback()}>{playing ? <PauseIcon size={15} weight="fill" /> : <PlayIcon size={15} weight="fill" />}</button>
      <input type="range" className="smoothing-slider audio-progress" aria-label={`Seek ${label}`} aria-valuetext={`${audioTime(position)} of ${audioTime(length)}`} min={0} max={length || 1} step={.1} value={Math.min(position, length)} disabled={failed || length <= 0} style={{ "--range-fill": `${length > 0 ? position / length * 100 : 0}%` } as CSSProperties} onChange={(event) => seek(Number(event.target.value))} />
      <span className="audio-time">{audioTime(position)} / {audioTime(length)}</span>
      <button className="button icon-button" type="button" aria-label={`${muted ? "Unmute" : "Mute"} ${label}`} aria-pressed={muted} title={muted ? "Unmute" : "Mute"} onClick={() => { if (audio.current) audio.current.muted = !muted; setMuted(!muted); }}>{muted ? <SpeakerSlashIcon size={16} /> : <SpeakerHighIcon size={16} />}</button>
    </div>
    {failed && <p className="data-error">Audio unavailable. <button className="text-button" onClick={() => { setFailed(false); audio.current?.load(); }}>Retry</button></p>}
  </div>;
}
