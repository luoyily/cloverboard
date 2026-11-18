interface RefreshTimerOptions {
  intervalMs: number;
  isHidden: () => boolean;
  now: () => number;
  setTimeout: (handler: () => void, delay: number) => number;
  clearTimeout: (id: number) => void;
  onPoll: () => void;
}

export interface RefreshTimer {
  resume: () => void;
  stop: () => void;
}

// Recursive timeouts keep the next poll relative to the last one. Browsers
// throttle or suspend hidden-page timers, so resume() is also called from
// visibilitychange/pageshow/focus to poll at once and rebuild the schedule.
export function startRefreshTimer({ intervalMs, isHidden, now, setTimeout, clearTimeout, onPoll }: RefreshTimerOptions): RefreshTimer {
  let lastPoll = now();
  let timer = 0;
  let stopped = false;
  const poll = () => { lastPoll = now(); onPoll(); };
  const schedule = () => { timer = setTimeout(tick, intervalMs); };
  function tick() {
    if (stopped) return;
    if (!isHidden()) poll();
    schedule();
  }
  function resume() {
    if (stopped || isHidden()) return;
    clearTimeout(timer);
    if (now() - lastPoll >= intervalMs) poll();
    schedule();
  }
  schedule();
  return { resume, stop: () => { stopped = true; clearTimeout(timer); } };
}
