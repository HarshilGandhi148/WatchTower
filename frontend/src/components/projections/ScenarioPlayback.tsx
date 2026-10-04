import { useEffect, useId, useRef, useState, type CSSProperties } from 'react';
import type { Projection } from '../../features/live/types';
import { supportedPaths } from '../../features/projection/aircraftPaths';
import { ageLabel, formatTime } from '../../lib/time';

export function ScenarioPlayback({
  projection,
  expectedSnapshotId,
  title,
  acceptedBy,
  minute,
  branch = 'proposed',
  onBranchChange,
  now,
  autoplay = Boolean(acceptedBy),
  focusOnOpen = true,
  reviewOpen = false,
  mode,
  timezone,
  onMinuteChange,
  onClose,
  onDetails,
}: {
  projection: Projection | undefined;
  expectedSnapshotId: string;
  title: string;
  acceptedBy?: string;
  minute: number;
  branch?: 'baseline' | 'proposed';
  onBranchChange?: (branch: 'baseline' | 'proposed') => void;
  now: number;
  autoplay?: boolean;
  focusOnOpen?: boolean;
  reviewOpen?: boolean;
  mode: 'LIVE' | 'TEST';
  timezone: string;
  onMinuteChange: (minute: number) => void;
  onClose: () => void;
  onDetails: () => void;
}) {
  const sliderId = useId();
  const close = useRef<HTMLButtonElement>(null);
  const update = { projection, expectedSnapshotId, minute, mode };
  const paths = supportedPaths(update);
  const [playback, setPlayback] = useState<{ at: number; from: number } | null>(
    () =>
      autoplay &&
      paths.length &&
      minute < 15 &&
      !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
        ? { at: Date.now(), from: minute }
        : null,
  );
  useEffect(() => {
    if (focusOnOpen) close.current?.focus();
  }, [focusOnOpen]);
  const playing = Boolean(playback && paths.length);
  useEffect(() => {
    const preference = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const stopOnReducedMotion = (event: MediaQueryListEvent) => {
      if (event.matches) setPlayback(null);
    };
    preference?.addEventListener?.('change', stopOnReducedMotion);
    return () =>
      preference?.removeEventListener?.('change', stopOnReducedMotion);
  }, []);
  useEffect(() => {
    if (!playback || !paths.length) return;
    let frame: number;
    let lastFrame = -1;
    const advance = (at: number) => {
      const next = Math.min(
        15,
        playback.from + (Date.now() - playback.at) / 1600,
      );
      // Follow browser frames, capped at 30 updates per second. The timeline
      // uses elapsed time, so a background tab cannot extend the scenario.
      const frameNumber = Math.floor(at / (1000 / 30));
      if (frameNumber !== lastFrame || next >= 15) {
        lastFrame = frameNumber;
        onMinuteChange(next);
      }
      if (next >= 15) setPlayback(null);
      else frame = window.requestAnimationFrame(advance);
    };
    frame = window.requestAnimationFrame(advance);
    return () => window.cancelAnimationFrame(frame);
  }, [playback, paths.length, onMinuteChange]);
  return (
    <section
      className="scenario-playback"
      data-playing={playing}
      data-accepted={Boolean(acceptedBy)}
      style={
        {
          '--scenario-progress': Math.min(1, Math.max(0, minute / 15)),
        } as CSSProperties
      }
      aria-label="Map projection"
      onKeyDown={(event) => {
        if (event.key === 'Escape' && !event.defaultPrevented) {
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="scenario-toolbar-meta">
        <div
          className="scenario-branch-controls"
          role="group"
          aria-label="Displayed scenario"
        >
          {(['baseline', 'proposed'] as const).map((view) => (
            <button
              key={view}
              aria-pressed={branch === view}
              onClick={() => onBranchChange?.(view)}
            >
              {view === 'baseline' ? 'Baseline' : 'Proposed'}
            </button>
          ))}
        </div>
        <span className="scenario-basis" title={title}>
          {paths.length
            ? `${projection?.aircraftPaths?.basis === 'illustrative' ? 'Illustrative' : 'Modeled'} paths · ${paths.length} aircraft`
            : 'Aircraft paths unavailable for this snapshot'}
        </span>
        <span
          className="scenario-snapshot"
          title={`Snapshot ${formatTime(projection?.snapshotAtUtc ?? null, timezone)}`}
        >
          Snapshot {ageLabel(projection?.snapshotAtUtc ?? null, now)}
        </span>
        <button
          className="button-quiet"
          aria-expanded={reviewOpen}
          aria-label="Open scenario review"
          onClick={onDetails}
        >
          Review
        </button>
        <button
          className="button-quiet"
          ref={close}
          onClick={onClose}
          aria-label="Close projection"
        >
          ×
        </button>
      </div>
      <div className="scenario-timeline">
        <button
          disabled={!paths.length}
          aria-label={playing ? 'Pause projection' : 'Play projection'}
          onClick={() => {
            if (playing) setPlayback(null);
            else {
              const from = minute >= 15 ? 0 : minute;
              onMinuteChange(from);
              setPlayback({ at: Date.now(), from });
            }
          }}
        >
          {playing ? 'Ⅱ' : '▶'}
        </button>
        <label className="sr-only" htmlFor={sliderId}>
          Projected time
        </label>
        <input
          id={sliderId}
          type="range"
          min={0}
          max={15}
          step={0.1}
          value={minute}
          aria-valuetext={`+${minute.toFixed(1)} projected minutes`}
          onChange={(event) => {
            setPlayback(null);
            onMinuteChange(event.currentTarget.valueAsNumber);
          }}
        />
        <output htmlFor={sliderId}>+{minute.toFixed(1)} min</output>
        <div
          className="scenario-jumps"
          role="group"
          aria-label="Projection time"
        >
          {[0, 5, 10, 15].map((time) => (
            <button
              key={time}
              aria-pressed={Math.abs(minute - time) < 0.05}
              onClick={() => {
                setPlayback(null);
                onMinuteChange(time);
              }}
            >
              {time === 0 ? 'Now' : `+${time} min`}
            </button>
          ))}
        </div>
      </div>
      <div className="scenario-legend">
        <span className="observed-key">● Observed</span>
        <span className="baseline-key">┄ Baseline</span>
        <span className="proposed-key">┄ Proposed</span>
        <span>
          {acceptedBy ? `Accepted by ${acceptedBy}` : 'Preview only'} · Live
          observations continue updating
        </span>
      </div>
    </section>
  );
}
