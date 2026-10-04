import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { TrafficMap } from '../dashboard/TrafficMap';
import { useClock, useDashboard } from '../../features/live/useDashboard';
import type { DashboardSnapshot, Projection } from '../../features/live/types';
import type {
  AgentActivity,
  AgentRole,
  EvidenceDetail,
} from '../../features/exchange/types';
import { sanitize, evidenceAircraft } from '../../features/exchange/adapter';
import type { DashboardClient } from '../../lib/connection';
import { loadEvidence } from '../../lib/spacetimeTransport';
import { formatTime } from '../../lib/time';
import { frontendConfig } from '../../lib/config';
import { currentRecommendations } from '../../features/exchange/recommendations';
import './mission.css';
import { RecommendationReviewControls } from './RecommendationReviewControls';
import { recommendationSummary } from '../../features/exchange/summary';
import { readableEvidence } from '../../features/exchange/evidence';

const emptyActivity: AgentActivity[] = [];
const roles: AgentRole[] = ['weather', 'congestion', 'validation'];
const names = {
  weather: 'Weather',
  congestion: 'Congestion',
  validation: 'Validator',
};
const jobs = {
  weather: 'Atmospheric context',
  congestion: 'Traffic & flow analysis',
  validation: 'Evidence & consistency',
};
function Bot({ role }: { role: AgentRole }) {
  return (
    <svg viewBox="0 0 64 64" fill="none" aria-hidden="true">
      <path d="M32 8v8M28 8h8M13 30H8v14h5m38-14h5v14h-5" />
      <rect x="14" y="18" width="36" height="34" rx="12" />
      <path d="M24 42h16" />
      <circle cx="25" cy="32" r="2" />
      <circle cx="39" cy="32" r="2" />
      {role === 'weather' ? (
        <path d="M22 58h20" />
      ) : role === 'validation' ? (
        <path d="m27 57 4 4 7-7" />
      ) : (
        <path d="M22 58h4m4 0h4m4 0h4" />
      )}
    </svg>
  );
}
export interface ReplayView {
  snapshot: DashboardSnapshot;
  now: number;
  controls: ReactNode;
  projection?: Projection;
}
export function MissionControl({
  client,
  replay,
  evidenceLoader = loadEvidence,
}: {
  client: DashboardClient;
  replay?: ReplayView;
  evidenceLoader?: (id: string) => Promise<EvidenceDetail>;
}) {
  const live = useDashboard(client),
    clock = useClock();
  const state = replay?.snapshot ?? live,
    now = replay?.now ?? clock;
  const exchange = state.exchange;
  const activity = exchange?.activity ?? emptyActivity;
  const cases = [...new Set(activity.map((a) => a.caseId))];
  const [reviewedIds, setReviewedIds] = useState<string[]>([]);
  const reports = currentRecommendations(exchange?.reports ?? [], now).filter(
    (r) =>
      !reviewedIds.includes(r.report_id) &&
      !exchange?.reviews?.some((review) => review.report_id === r.report_id) &&
      (replay ||
        (r.recommendation_kind === 'demo' &&
          !!r.aircraft_recommendations?.length)),
  );
  const [reportId, setReportId] = useState('');
  const report = reports.find((r) => r.report_id === reportId) ?? reports[0];
  const selectedCase = report?.case_id ?? cases.at(-1);
  const messages = activity.filter((a) => a.caseId === selectedCase);
  const reassessing = Boolean(
    report && messages.some((a) => a.round > report.assessment_round),
  );
  const latest = messages.at(-1);
  const [conversation, setConversation] = useState(false);
  const [accepted, setAccepted] = useState('');
  const [minute, setMinute] = useState(0);
  const [pathsPlaying, setPathsPlaying] = useState(false);
  const projection = replay?.projection;
  const pathPreview = Boolean(
    report &&
    !reassessing &&
    state.connection === 'ready' &&
    Date.parse(report.created_at_utc) <= now &&
    accepted === report.report_id &&
    projection?.snapshotId === report.snapshot_id &&
    Date.parse(report.expires_at_utc) > now,
  );
  useEffect(() => {
    if (!pathPreview || !pathsPlaying || minute >= 15) return;
    const timer = setInterval(
      () => setMinute((m) => Math.min(15, m + 0.1)),
      160,
    );
    return () => clearInterval(timer);
  }, [pathPreview, pathsPlaying, minute]);
  const [selected, setSelected] = useState<AgentActivity | null>(null);
  const [detail, setDetail] = useState<EvidenceDetail | null>(null);
  const highlights = useMemo(
    () =>
      selected && detail
        ? evidenceAircraft(detail.content, selected.evidenceIds)
        : (report?.aircraft_recommendations?.map(
            (plane) => plane.aircraft_id,
          ) ?? []),
    [selected, detail, report],
  );
  const [error, setError] = useState('');
  const log = useRef<HTMLDivElement>(null);
  const followLog = useRef(true);
  useEffect(() => {
    if (log.current && followLog.current)
      log.current.scrollTop = log.current.scrollHeight;
  }, [latest?.id]);
  const returnFocus = useRef<HTMLElement | null>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const seen = useRef<Set<string> | null>(null);
  const [newIds, setNewIds] = useState<string[]>([]);
  useEffect(() => {
    const ids = new Set(activity.map((a) => a.id));
    const fresh =
      state.connection === 'ready' && seen.current
        ? [...ids].filter((id) => !seen.current!.has(id))
        : [];
    seen.current = state.connection === 'ready' ? ids : null;
    if (fresh.length || state.connection !== 'ready' || ids.size === 0)
      setNewIds(fresh);
  }, [activity, state.connection]);
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    closeButton.current?.focus();
    evidenceLoader(selected.snapshotId)
      .then((value) => {
        if (!cancelled) setDetail(value);
      })
      .catch(() => {
        if (!cancelled)
          setError(
            'Evidence unavailable. The source record has not loaded. Close and retry.',
          );
      });
    return () => {
      cancelled = true;
    };
  }, [selected, evidenceLoader]);
  const close = () => {
    setSelected(null);
    returnFocus.current?.focus();
  };
  const inspect = (message: AgentActivity) => {
    returnFocus.current = document.activeElement as HTMLElement;
    setDetail(null);
    setError('');
    setSelected(message);
  };
  const expired = report && Date.parse(report.expires_at_utc) <= now;
  const evidence = detail ? readableEvidence(detail.content) : undefined;
  return (
    <main className="mission">
      <a className="skip-link" href="#agent-exchange">
        Skip to agent exchange
      </a>
      <header className="mission-header">
        <div className="mission-brand">
          <span className="tower-mark">
            W<span>◉</span>
          </span>
          <div>
            <h1>WatchTower</h1>
            <p>Airport flow overview</p>
          </div>
        </div>
        <div className="mission-airport">
          <strong>
            DTW <span>/ KDTW</span>
          </strong>
          <small>Detroit Metropolitan · 40 NM</small>
        </div>
        <nav aria-label="Workspace">
          <span className={`mission-mode ${replay ? 'test' : ''}`}>
            {replay
              ? 'TEST REPLAY'
              : state.connection === 'ready'
                ? 'LIVE DATA'
                : 'AWAITING CONNECTION'}
          </span>
          {replay ? (
            <a href={location.port === '5174' ? 'http://localhost:5173' : '/'}>
              Live workspace ↗
            </a>
          ) : (
            <a
              href={
                location.hostname === 'localhost' ||
                location.hostname === '127.0.0.1'
                  ? 'http://localhost:5174'
                  : '/demo/'
              }
            >
              Demo replay ↗
            </a>
          )}
        </nav>
      </header>
      <section className="mission-health" aria-label="Data sources">
        <span className="eyebrow">SOURCE NETWORK</span>
        {state.sources.length ? (
          state.sources.map((s) => (
            <span
              key={s.id}
              title={s.detail}
              className={`source-pill feed-${s.status}`}
            >
              <i />
              {s.name}
              <b>{s.status}</b>
            </span>
          ))
        ) : (
          <span>No provider observations connected</span>
        )}
        <time>
          {formatTime(
            new Date(now).toISOString(),
            frontendConfig.displayTimezone,
          )}{' '}
          <small>Detroit</small>
        </time>
      </section>
      {replay && (
        <div className="replay-banner">
          <strong>Recorded TEST scenario</strong>
          <span>
            Weather test double · synthetic evidence · no live observations or
            controller decisions
          </span>
        </div>
      )}
      {replay?.controls}
      <div className="mission-grid">
        <section className="mission-map-panel" aria-labelledby="map-heading">
          <header className="mission-panel-heading">
            <div>
              <span className="eyebrow">01 / OBSERVE</span>
              <h2 id="map-heading">Airspace overview</h2>
            </div>
            <span className="tag">
              {replay ? 'TEST aircraft' : 'Observed aircraft'}
            </span>
          </header>
          <div className="mission-stats">
            <div>
              <strong>{state.nearbyCount ?? '—'}</strong>
              <span>Nearby aircraft</span>
            </div>
            <div>
              <strong>{state.weather ? 'Available' : 'Unknown'}</strong>
              <span>Weather evidence</span>
            </div>
            <div>
              <strong>{cases.length}</strong>
              <span>Recorded cases</span>
            </div>
          </div>
          <div className="mission-map">
            <TrafficMap
              aircraft={state.aircraft}
              now={now}
              disconnected={state.connection !== 'ready'}
              timezone={frontendConfig.displayTimezone}
              mode={state.mode}
              initialView="traffic"
              highlightedAircraftIds={highlights}
              mapProjection={
                pathPreview && report
                  ? {
                      projection,
                      expectedSnapshotId: report.snapshot_id,
                      minute,
                      mode: state.mode,
                      branch: 'proposed',
                    }
                  : null
              }
            />
          </div>
          {pathPreview && (
            <div className="path-controls">
              <strong>
                Illustrative TEST paths · +{minute.toFixed(1)} min
              </strong>
              <button onClick={() => setPathsPlaying(!pathsPlaying)}>
                {pathsPlaying && minute < 15 ? 'Pause paths' : 'Play paths'}
              </button>
              <input
                aria-label="Projection minute"
                type="range"
                min="0"
                max="15"
                step="0.1"
                value={minute}
                onChange={(e) => {
                  setPathsPlaying(false);
                  setMinute(Number(e.target.value));
                }}
              />
              <button onClick={() => setAccepted('')}>Close preview</button>
              <small>
                Hypothetical route illustration, not a flight prediction or
                clearance. Observed aircraft remain unchanged.
              </small>
            </div>
          )}
          <footer className="mission-map-caption">
            <span className="observed-key" />{' '}
            {replay
              ? 'TEST positions from the selected scenario'
              : 'Live observations continue independently of the selected case'}
            <small>Nearby aircraft are not verified airport demand.</small>
          </footer>
          <section className="mission-context">
            <span className="eyebrow">ENVIRONMENT</span>
            <h3>{state.weather?.summary ?? 'Awaiting weather observations'}</h3>
            <div>
              {state.weather?.facts.map((f) => (
                <span key={f.label}>
                  <small>{f.label}</small>
                  <strong>{f.value}</strong>
                </span>
              ))}
            </div>
            <p>
              {state.eventsStatus === 'unavailable'
                ? 'FAA event coverage unavailable.'
                : state.events.length
                  ? `${state.events.length} published airport events.`
                  : 'No airport event in the selected feed.'}
            </p>
          </section>
        </section>
        <section
          id="agent-exchange"
          className="exchange-panel"
          aria-labelledby="exchange-heading"
        >
          <header className="mission-panel-heading">
            <div>
              <span className="eyebrow">AGENT ADVICE</span>
              <h2 id="exchange-heading">Recommendations</h2>
              <small>
                Latest 10 minutes · expired advice clears automatically
              </small>
            </div>
          </header>
          <div className="agent-stage">
            <svg
              className={`agent-links ${latest && newIds.includes(latest.id) ? 'transmitting' : ''}`}
              key={latest?.id}
              viewBox="0 0 600 70"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <path d="M95 35H505" />
              <path className="packet" d="M95 35H505" />
            </svg>
            {roles.map((role) => (
              <div
                key={role}
                className={`agent-node agent-${role} ${latest?.agent === role && newIds.includes(latest.id) ? 'agent-active' : ''}`}
              >
                <div className="bot-avatar">
                  <Bot role={role} />
                </div>
                <strong>{names[role]}</strong>
                <small>{jobs[role]}</small>
                <span>
                  {messages.some((m) => m.agent === role)
                    ? 'Recorded activity'
                    : 'Awaiting evidence'}
                </span>
              </div>
            ))}
          </div>
          <div className="advice-list" aria-label="Recommendations">
            {!reports.length && (
              <div className="exchange-empty">
                <h3>
                  {replay
                    ? 'Agents are assessing the situation…'
                    : 'Waiting for agent advice'}
                </h3>
                <p>
                  {replay
                    ? 'Advice will appear automatically when the validator finishes.'
                    : 'Agents are watching live traffic. Aircraft recommendations appear on alternating assessment cycles.'}
                </p>
              </div>
            )}
            {reports.map((r) => (
              <article key={r.report_id} className="advice-item">
                <button
                  className="advice-card"
                  aria-expanded={
                    conversation && report?.report_id === r.report_id
                  }
                  onClick={() => {
                    setReportId(r.report_id);
                    setConversation(true);
                  }}
                >
                  <span>
                    {Date.parse(r.expires_at_utc) <= now
                      ? 'Expired'
                      : r.outcome === 'insufficient_data'
                        ? 'Limited data'
                        : r.outcome.replaceAll('_', ' ')}{' '}
                    · Round {r.assessment_round}
                    {' · '}
                    {formatTime(
                      r.created_at_utc,
                      frontendConfig.displayTimezone,
                    )}
                  </span>
                  <strong>{recommendationSummary(r.suggestion)}</strong>
                  <small>View agent conversation →</small>
                </button>
                {!!r.aircraft_recommendations?.length && (
                  <div className="aircraft-recommendations">
                    <span className="eyebrow">
                      DEMO FORECAST · 2 MIN AFTER CAPTURE
                    </span>
                    {r.aircraft_recommendations.map((plane) => (
                      <section key={plane.aircraft_id}>
                        <strong>✈ {plane.label}</strong>
                        <p>{plane.recommendation}</p>
                        <small>{plane.prediction}</small>
                      </section>
                    ))}
                  </div>
                )}
                <RecommendationReviewControls
                  report={r}
                  review={exchange?.reviews?.find(
                    (review) =>
                      review.report_id === r.report_id &&
                      review.snapshot_id === r.snapshot_id &&
                      review.assessment_round === r.assessment_round,
                  )}
                  enabled={
                    !replay &&
                    state.connection === 'ready' &&
                    !activity.some(
                      (a) =>
                        a.caseId === r.case_id && a.round > r.assessment_round,
                    )
                  }
                  onReviewed={(id) =>
                    setReviewedIds((ids) => [...ids, id].slice(-100))
                  }
                />
              </article>
            ))}
          </div>
          {report &&
            report.outcome === 'supported' &&
            !reassessing &&
            state.connection === 'ready' &&
            Date.parse(report.created_at_utc) <= now &&
            !expired &&
            replay &&
            projection?.snapshotId === report.snapshot_id && (
              <div className="advice-action">
                <button
                  onClick={() => {
                    setAccepted(report.report_id);
                    setMinute(0);
                    setPathsPlaying(
                      !window.matchMedia?.('(prefers-reduced-motion: reduce)')
                        .matches,
                    );
                  }}
                >
                  Accept for TEST path preview
                </button>
                <small>
                  Local demo selection only. Routes are illustrative.
                </small>
              </div>
            )}
          {conversation && report && (
            <>
              <div className="conversation-heading">
                <h3>Agent conversation</h3>
                <button onClick={() => setConversation(false)}>
                  Close conversation
                </button>
              </div>
              <div
                className="exchange-log"
                ref={log}
                onScroll={(e) => {
                  const el = e.currentTarget;
                  followLog.current =
                    el.scrollHeight - el.scrollTop - el.clientHeight < 40;
                }}
                role="region"
                aria-label="Assessment summaries"
                tabIndex={0}
              >
                {!messages.length && (
                  <div className="exchange-empty">
                    <span>◎</span>
                    <h3>Ready to listen.</h3>
                    <p>
                      Saved agent assessments will appear here when a case
                      begins. Open the TEST replay to explore the complete
                      exchange.
                    </p>
                  </div>
                )}
                {messages.map((m) => (
                  <article
                    key={m.id}
                    className={`exchange-message agent-${m.agent} ${newIds.includes(m.id) ? 'message-new' : ''}`}
                  >
                    <div className="message-marker">
                      {m.agent === 'weather'
                        ? 'W'
                        : m.agent === 'congestion'
                          ? 'C'
                          : 'V'}
                    </div>
                    <div className="message-body">
                      <header>
                        <strong>{names[m.agent]}</strong>
                        <span>{m.event}</span>
                        <time>
                          {formatTime(m.atUtc, frontendConfig.displayTimezone)}
                        </time>
                      </header>
                      <p>{m.summary}</p>
                      {!!m.details?.length && (
                        <details className="message-details">
                          <summary>Evidence & coverage details</summary>
                          <ul>
                            {m.details.map((value, index) => (
                              <li key={index}>{value}</li>
                            ))}
                          </ul>
                        </details>
                      )}
                      <footer>
                        <button onClick={() => inspect(m)}>
                          {m.evidenceIds.length} evidence references ↗
                        </button>
                        <small>
                          Round {m.round} ·{' '}
                          {m.provenance === 'TEST'
                            ? 'TEST summary'
                            : 'Stored assessment summary'}
                        </small>
                      </footer>
                    </div>
                  </article>
                ))}
              </div>
            </>
          )}
          {!!exchange?.issues.length && (
            <div className="exchange-warning">{exchange.issues.join(' ')}</div>
          )}
          {conversation && report && (
            <section
              className={`validator-result outcome-${report?.outcome ?? 'waiting'}`}
              aria-label="Validation result"
            >
              <div className="result-heading">
                <span className="eyebrow">03 / VALIDATE</span>
                <span>
                  {expired
                    ? 'EXPIRED'
                    : report
                      ? state.connection !== 'ready'
                        ? 'CACHED REPORT'
                        : reassessing
                          ? 'PREVIOUS ROUND · REASSESSING'
                          : report.outcome === 'insufficient_data'
                            ? 'LIMITED DATA'
                            : report.outcome.replaceAll('_', ' ').toUpperCase()
                      : 'AWAITING REPORT'}
                </span>
              </div>
              <h3>
                {expired
                  ? 'This assessment is no longer current.'
                  : (report?.suggestion ??
                    'Evidence first. Suggestion follows.')}
              </h3>
              {!report && (
                <p>
                  Receiving a candidate confirms delivery. The validator must
                  finish and save its checks before a result appears.
                </p>
              )}
              {report && (
                <>
                  <div className="validation-checks">
                    {report.checks.map((c) => (
                      <button
                        key={c.name}
                        title={c.reason}
                        onClick={() => {
                          inspect({
                            id: report.report_id,
                            caseId: report.case_id,
                            snapshotId: report.snapshot_id,
                            round: report.assessment_round,
                            agent: 'validation',
                            event: c.name,
                            atUtc: report.created_at_utc,
                            summary: c.reason,
                            evidenceIds: c.evidence_ids,
                            provenance: state.mode,
                          });
                        }}
                        className={`check-${c.status}`}
                      >
                        <span>
                          {c.status === 'pass'
                            ? '✓'
                            : c.status === 'fail'
                              ? '!'
                              : '—'}
                        </span>
                        {c.name}
                        <small>{c.reason}</small>
                      </button>
                    ))}
                  </div>
                  <details>
                    <summary>
                      Assumptions & limitations ({report.limitations.length})
                    </summary>
                    <ul>
                      {report.limitations.map((l) => (
                        <li key={l}>{l}</li>
                      ))}
                    </ul>
                  </details>
                  <small>
                    Informational review suggestion · no operational approval
                  </small>
                </>
              )}
            </section>
          )}
        </section>
      </div>
      <footer className="mission-footer">
        <span>Three agents. Shared evidence. Human authority.</span>
        <span>SpacetimeDB · Fetch.ai / uAgents · DTW prototype</span>
      </footer>
      {selected && (
        <div className="evidence-scrim" onClick={close}>
          <section
            className="evidence-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="evidence-title"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === 'Escape') close();
              if (e.key === 'Tab') {
                const nodes = e.currentTarget.querySelectorAll<HTMLElement>(
                  'button, summary, a, [tabindex="0"]',
                );
                const first = nodes[0],
                  last = nodes[nodes.length - 1];
                if (e.shiftKey && document.activeElement === first) {
                  e.preventDefault();
                  last?.focus();
                } else if (!e.shiftKey && document.activeElement === last) {
                  e.preventDefault();
                  first?.focus();
                }
              }
            }}
          >
            <header>
              <div>
                <span className="eyebrow">EVIDENCE INSPECTOR</span>
                <h2 id="evidence-title">What the agents saw</h2>
              </div>
              <button ref={closeButton} onClick={close}>
                Close
              </button>
            </header>
            <p>{selected.summary}</p>
            <dl>
              <dt>Review</dt>
              <dd>
                Round {selected.round} · {selected.provenance}
              </dd>
              <dt>Recorded</dt>
              <dd>
                {formatTime(selected.atUtc, frontendConfig.displayTimezone)}
              </dd>
            </dl>
            {error ? (
              <p role="alert">{error}</p>
            ) : !detail ? (
              <p role="status">Loading copied evidence…</p>
            ) : (
              <>
                <dl>
                  <dt>Time window</dt>
                  <dd>
                    {formatTime(
                      detail.windowStartUtc,
                      frontendConfig.displayTimezone,
                    )}{' '}
                    →{' '}
                    {formatTime(
                      detail.windowEndUtc,
                      frontendConfig.displayTimezone,
                    )}
                  </dd>
                </dl>
                <h3>Weather and traffic at a glance</h3>
                <div className="readable-evidence-facts">
                  {evidence?.facts.map((fact, i) => (
                    <div className="evidence-fact" key={i}>
                      <span>{fact.label}</span>
                      <strong>{fact.value}</strong>
                    </div>
                  ))}
                </div>
                {!!evidence?.planes.length && (
                  <>
                    <h3>Aircraft near KDTW</h3>
                    <p>
                      Closest observed aircraft in this saved snapshot. Altitude
                      is in feet, speed in knots, and distance in nautical
                      miles.
                    </p>
                    <div className="evidence-aircraft-list">
                      {evidence.planes.map((plane) => (
                        <article key={plane.id}>
                          <strong>✈ {plane.label}</strong>
                          <span>{plane.distance} from KDTW</span>
                          <small>
                            {plane.altitude} · {plane.speed} · heading{' '}
                            {plane.heading}
                          </small>
                        </article>
                      ))}
                    </div>
                  </>
                )}
                <p>
                  This is the saved view used for the recommendation. The live
                  map continues updating separately.
                </p>
                <details>
                  <summary>Technical source record</summary>
                  <p>Snapshot: {selected.snapshotId}</p>
                  <ul>
                    {selected.evidenceIds.map((id) => (
                      <li key={id}>
                        <code>{id}</code>
                      </li>
                    ))}
                  </ul>
                  <pre tabIndex={0}>
                    {JSON.stringify(sanitize(detail.content), null, 2)}
                  </pre>
                </details>
              </>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
