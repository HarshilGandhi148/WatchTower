import { useEffect, useId, useRef, useState } from 'react';
import type { Projection } from '../../features/live/types';
import { supportedPaths } from '../../features/projection/aircraftPaths';
import { formatTime } from '../../lib/time';

export function ScenarioPlayback({
  projection,
  expectedSnapshotId,
  title,
  acceptedBy,
  minute,
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
      acceptedBy &&
      paths.length &&
      minute < 15 &&
      !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
        ? { at: Date.now(), from: minute }
        : null,
  );
  useEffect(() => {
    close.current?.focus();
  }, []);
  useEffect(() => {
    if (!playback) return;
    const timer = window.setInterval(() => {
      const next = Math.min(
        15,
        playback.from + (Date.now() - playback.at) / 1600,
      );
      onMinuteChange(next);
      if (next >= 15) setPlayback(null);
    }, 100);
    return () => window.clearInterval(timer);
  }, [playback, onMinuteChange]);
  return (
    <section
      className="scenario-playback"
      aria-label="Map projection"
      onKeyDown={(event) => {
        if (event.key === 'Escape' && !event.defaultPrevented) {
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="scenario-heading">
        <div>
          <span className="eyebrow">
            {acceptedBy ? 'Accepted scenario' : 'Scenario preview'}
          </span>
          <strong>{title}</strong>
        </div>
        <button ref={close} onClick={onClose} aria-label="Close projection">
          ×
        </button>
      </div>
      {acceptedBy && (
        <p className="scenario-decision" role="status">
          Accepted scenario · decision recorded by {acceptedBy}
        </p>
      )}
      <div className="scenario-provenance">
        <span>
          {paths.length
            ? `${projection?.aircraftPaths?.basis === 'illustrative' ? 'Illustrative' : 'Modeled'} paths · ${paths.length} aircraft`
            : 'Aircraft paths unavailable for this snapshot'}
        </span>
        <span>
          Snapshot {formatTime(projection?.snapshotAtUtc ?? null, timezone)}
        </span>
      </div>
      <div className="scenario-timeline">
        <button
          disabled={!paths.length}
          aria-label={playback ? 'Pause projection' : 'Play projection'}
          onClick={() => {
            if (playback) setPlayback(null);
            else {
              const from = minute >= 15 ? 0 : minute;
              onMinuteChange(from);
              setPlayback({ at: Date.now(), from });
            }
          }}
        >
          {playback ? 'Ⅱ' : '▶'}
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
        <button onClick={onDetails}>Scenario details</button>
      </div>
      <div className="scenario-legend">
        <span className="observed-key">● Observed</span>
        <span className="baseline-key">┄ Baseline</span>
        <span className="proposed-key">┄ Proposed paths</span>
        <span>Live observations continue updating</span>
      </div>
    </section>
  );
}
