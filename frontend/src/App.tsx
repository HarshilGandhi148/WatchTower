import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { AdvisoryCard } from './components/advisories/AdvisoryCard';
import { LiveOverview } from './components/dashboard/LiveOverview';
import { DecisionHistory } from './components/history/DecisionHistory';
import { ProjectionDrawer } from './components/projections/ProjectionDrawer';
import { Popover } from './components/workspace/Popover';
import { WorkspaceDrawer } from './components/workspace/WorkspaceDrawer';
import { activeAdvisories } from './features/advisories/policy';
import type { Advisory } from './features/live/types';
import { useClock, useDashboard } from './features/live/useDashboard';
import { createDashboardClient, type DashboardClient } from './lib/connection';
import { frontendConfig } from './lib/config';
import { ageLabel, formatTime } from './lib/time';

const liveClient = createDashboardClient();
interface Selection {
  projectionId: string;
  snapshotId: string;
  title: string;
  acceptedBy?: string;
  returnPage?: Page;
}
type SidePanel =
  | { kind: 'projection' }
  | { kind: 'advisory'; id: string; version: number; editing: boolean };
type Page = 'overview' | 'history';
const currentPage = (): Page =>
  window.location.hash === '#/history' ? 'history' : 'overview';
export default function App({
  client = liveClient,
  tools,
}: {
  client?: DashboardClient;
  tools?: ReactNode;
}) {
  const sourcesTitle = useId();
  const queueTitle = useId();
  const overviewId = useId();
  const historyId = useId();
  const historyButton = useRef<HTMLButtonElement>(null);
  const queueHeading = useRef<HTMLHeadingElement>(null);
  const sideReturnFocus = useRef<HTMLElement | null>(null);
  const projectionReturnFocus = useRef<HTMLElement | null>(null);
  const pendingFocus = useRef<{ target: HTMLElement | null } | null>(null);
  const state = useDashboard(client);
  const now = useClock();
  const timezone = frontendConfig.displayTimezone;
  const [page, setPage] = useState<Page>(currentPage);
  const [minute, setMinute] = useState(0);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [sidePanel, setSidePanel] = useState<SidePanel | null>(null);
  useEffect(() => {
    const sync = () => {
      setPage(currentPage());
      setSidePanel(null);
    };
    window.addEventListener('popstate', sync);
    window.addEventListener('hashchange', sync);
    return () => {
      window.removeEventListener('popstate', sync);
      window.removeEventListener('hashchange', sync);
    };
  }, []);
  const navigate = (next: Page) => {
    if (currentPage() !== next) window.history.pushState(null, '', `#/${next}`);
    setPage(next);
    setSidePanel(null);
  };
  const active = activeAdvisories(state.advisories, now);
  const revising = state.advisories.filter((a) => a.status === 'revalidating');
  const reviewed =
    sidePanel?.kind === 'advisory'
      ? state.advisories.find(
          (a) => a.id === sidePanel.id && a.version === sidePanel.version,
        )
      : undefined;
  const currentFocus = () => {
    const element = document.activeElement;
    return element instanceof HTMLElement &&
      element !== document.body &&
      element !== document.documentElement
      ? element
      : null;
  };
  const restoreFocus = useCallback((target: HTMLElement | null) => {
    if (
      target?.isConnected &&
      !target.closest('[inert]') &&
      !target.closest('[hidden]') &&
      !(target instanceof HTMLButtonElement && target.disabled)
    )
      target.focus();
    else queueHeading.current?.focus();
  }, []);
  useEffect(() => {
    if (pendingFocus.current) {
      restoreFocus(pendingFocus.current.target);
      pendingFocus.current = null;
    }
  }, [selection, sidePanel, page, restoreFocus]);
  const closeProjection = () => {
    setSelection(null);
    setSidePanel(null);
    if (selection?.returnPage === 'history') navigate('history');
    else pendingFocus.current = { target: projectionReturnFocus.current };
  };
  const closeSidePanel = () => {
    setSidePanel(null);
    pendingFocus.current = { target: sideReturnFocus.current };
  };
  const openReview = (advisory: Advisory, editing = false) => {
    sideReturnFocus.current = currentFocus();
    setSelection(null);
    setSidePanel({
      kind: 'advisory',
      id: advisory.id,
      version: advisory.version,
      editing,
    });
  };
  const openProjection = (advisory: Advisory, approved = false) => {
    if (!advisory.projectionId) return;
    projectionReturnFocus.current =
      sidePanel?.kind === 'advisory' ? sideReturnFocus.current : currentFocus();
    navigate('overview');
    setMinute(0);
    setSelection({
      projectionId: advisory.projectionId,
      snapshotId: advisory.snapshotId,
      title: advisory.title,
      acceptedBy: approved
        ? (state.session?.displayName ?? 'Controller')
        : undefined,
    });
  };
  const openEdit = (advisory: Advisory) => openReview(advisory, true);
  const openAcceptedProjection = (advisory: Advisory) =>
    openProjection(advisory, true);
  const sources = state.sources.length
    ? state.sources
    : [
        'Aviation weather',
        'Nearby positions',
        'FAA events',
        'Flight demand',
      ].map((name) => ({
        id: name,
        name,
        status: 'unknown',
        sourceAtUtc: null,
        fetchedAtUtc: null,
        detail: 'No source-health records received.',
        attribution: '',
        attributionUrl: undefined,
      }));
  const card = (advisory: Advisory, detail = false) => (
    <AdvisoryCard
      key={`${advisory.id}-${advisory.version}-${detail && sidePanel?.kind === 'advisory' && sidePanel.editing}`}
      advisory={advisory}
      state={state}
      client={client}
      now={now}
      timezone={timezone}
      compact={!detail}
      initialEditing={
        detail && sidePanel?.kind === 'advisory' && sidePanel.editing
      }
      onReview={openReview}
      onEdit={openEdit}
      onApproveRecorded={openAcceptedProjection}
      onPreview={openProjection}
    />
  );
  return (
    <main className="dashboard workspace-shell">
      <a
        className="skip-link"
        href="#/overview"
        onClick={(event) => {
          event.preventDefault();
          if (sidePanel || page !== 'overview')
            pendingFocus.current = { target: queueHeading.current };
          else queueHeading.current?.focus();
          navigate('overview');
        }}
      >
        Skip to advisories
      </a>
      <header className="app-header">
        <div className="brand">
          <svg className="brand-symbol" viewBox="0 0 40 40" aria-hidden="true">
            <path d="M11 9h18l-3 10H14L11 9Zm5 10v12m8-12v12M12 31h16M8 5h24" />
            <path d="M18 10v8m4-8v8" />
          </svg>
          <div>
            <h1>WatchTower</h1>
            <p className="header-subtitle">Airport flow coordination</p>
          </div>
        </div>
        <div className="airport-identity">
          <strong>
            DTW <span>/ KDTW</span>
          </strong>
          <span>Detroit Metropolitan</span>
        </div>
        <nav className="app-nav" aria-label="Dashboard sections">
          <button
            aria-current={page === 'overview' ? 'page' : undefined}
            onClick={() => {
              if (sidePanel || page !== 'overview')
                pendingFocus.current = {
                  target: document.getElementById(overviewId),
                };
              else document.getElementById(overviewId)?.focus();
              navigate('overview');
            }}
          >
            Overview
          </button>
          <button
            onClick={() => {
              if (sidePanel || page !== 'overview')
                pendingFocus.current = { target: queueHeading.current };
              else queueHeading.current?.focus();
              navigate('overview');
            }}
          >
            Advisories <span>{active.length}</span>
          </button>
          <button
            ref={historyButton}
            aria-current={page === 'history' ? 'page' : undefined}
            onClick={() => navigate('history')}
          >
            History
          </button>
        </nav>
        <div className="header-status">
          {state.mode === 'TEST' ? (
            <Popover
              className="mode-info"
              summary={
                <>
                  <span
                    className="health-dot status-limited"
                    aria-hidden="true"
                  />
                  Demo mode
                </>
              }
            >
              <strong>Sample data</strong>
              <p>
                Aircraft, weather and advisories are simulated. No live provider
                or operational system is connected.
              </p>
              <p>
                Decisions are recorded only in this local preview. Controller
                roles are illustrative.
              </p>
            </Popover>
          ) : (
            <span
              className={`connection-indicator ${state.connection === 'ready' ? 'connected' : ''}`}
            >
              <span
                className={`health-dot ${state.connection === 'ready' ? 'status-fresh' : 'status-unavailable'}`}
                aria-hidden="true"
              />
              {state.connection === 'ready' ? 'Live connection' : 'Offline'}
            </span>
          )}
          {state.session && (
            <span className="controller-identity">
              {state.session.displayName}
            </span>
          )}
        </div>
      </header>
      <div className="workspace-statusbar">
        <section className="source-health" aria-labelledby={sourcesTitle}>
          <h2 id={sourcesTitle} className="sr-only">
            Source health
          </h2>
          {sources.map((source) => (
            <Popover
              key={source.id}
              className="source-badge"
              group={sourcesTitle}
              summary={
                <>
                  <span
                    className={`health-dot status-${source.status}`}
                    aria-hidden="true"
                  />
                  <span>{source.name}</span>
                  <span className="source-status">{source.status}</span>
                  <span className="source-chevron" aria-hidden="true">
                    ⌄
                  </span>
                </>
              }
            >
              <p>{source.detail}</p>
              <small>
                Source time: {formatTime(source.sourceAtUtc, timezone)}
              </small>
              <small>
                Last successful fetch:{' '}
                {formatTime(source.fetchedAtUtc, timezone)}
              </small>
              {source.attribution && (
                <p>
                  {source.attributionUrl &&
                  /^https?:\/\//.test(source.attributionUrl) ? (
                    <a
                      href={source.attributionUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {source.attribution}
                    </a>
                  ) : (
                    source.attribution
                  )}
                </p>
              )}
            </Popover>
          ))}
        </section>
        <div className="airport-time">
          <time dateTime={new Date(now).toISOString()}>
            {formatTime(new Date(now).toISOString(), timezone)}
          </time>
          <small>
            Detroit local · Updated{' '}
            {state.refreshedAtUtc
              ? ageLabel(state.refreshedAtUtc, now)
              : '— awaiting data'}
          </small>
        </div>
      </div>
      {state.connection !== 'ready' && (
        <div className="connection-notice" role="status">
          <strong>
            {state.connection === 'unconfigured'
              ? 'Connection unavailable'
              : 'Connection requires attention'}
          </strong>
          <span>{state.connectionMessage}</span>
        </div>
      )}
      <div className="workspace-main">
        <div
          className={`dashboard-grid ${sidePanel ? 'review-open' : ''}`}
          hidden={page !== 'overview'}
        >
          <LiveOverview
            id={overviewId}
            scenario={
              selection
                ? {
                    ...selection,
                    projection: state.projections.find(
                      (p) => p.id === selection.projectionId,
                    ),
                    minute,
                  }
                : null
            }
            playbackVisible={page === 'overview'}
            onMinuteChange={setMinute}
            onCloseProjection={closeProjection}
            onScenarioDetails={() => {
              sideReturnFocus.current = currentFocus();
              setSidePanel({ kind: 'projection' });
            }}
            state={state}
            now={now}
            timezone={timezone}
          />
          <div className="advisory-slot">
            <section
              className="panel advisory-panel"
              hidden={Boolean(sidePanel)}
              aria-labelledby={queueTitle}
            >
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Controller review</p>
                  <h2 id={queueTitle} ref={queueHeading} tabIndex={-1}>
                    Advisories
                  </h2>
                </div>
                <span className="count-badge">{active.length}</span>
              </div>
              <div
                className="advisory-scroll"
                role="region"
                aria-label="Advisory list"
                tabIndex={0}
              >
                {!active.length && (
                  <div className="advisory-empty">
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <rect x="5" y="3" width="14" height="18" rx="3" />
                      <path d="M9 8h6m-6 4h6m-6 4h3" />
                    </svg>
                    <h3>No advisories supported by current data</h3>
                    <p>
                      {state.connection === 'ready'
                        ? 'Review source health for coverage and current evidence.'
                        : 'Waiting for a synchronized connection.'}
                    </p>
                  </div>
                )}
                {active.slice(0, 3).map((advisory) => card(advisory))}
                {active.length > 3 && (
                  <details className="more-advisories">
                    <summary>{active.length - 3} additional advisories</summary>
                    {active.slice(3).map((advisory) => card(advisory))}
                  </details>
                )}
                {revising.map((a) => (
                  <p
                    className="revalidation-notice"
                    role="status"
                    key={`${a.id}-${a.version}`}
                  >
                    {a.title} · v{a.version} awaiting revalidation. Decisions
                    unavailable.
                  </p>
                ))}
              </div>
            </section>
            {sidePanel?.kind === 'projection' && selection && (
              <ProjectionDrawer
                key={`${selection.projectionId}-${selection.snapshotId}`}
                projection={state.projections.find(
                  (p) => p.id === selection.projectionId,
                )}
                expectedSnapshotId={selection.snapshotId}
                title={selection.title}
                acceptedBy={selection.acceptedBy}
                now={now}
                timezone={timezone}
                minute={minute}
                onMinuteChange={setMinute}
                onClose={closeSidePanel}
              />
            )}
            {sidePanel?.kind === 'advisory' && (
              <WorkspaceDrawer
                key={`${sidePanel.id}-${sidePanel.version}-${sidePanel.editing}`}
                title="Advisory details"
                onClose={closeSidePanel}
              >
                {reviewed ? (
                  card(reviewed, true)
                ) : (
                  <p role="status">
                    This advisory version is no longer available. Review the
                    current queue.
                  </p>
                )}
              </WorkspaceDrawer>
            )}
          </div>
        </div>
        {page === 'history' && (
          <DecisionHistory
            id={historyId}
            state={state}
            now={now}
            timezone={timezone}
            onPreview={(entry) => {
              if (!entry.projectionId) return;
              projectionReturnFocus.current = historyButton.current;
              navigate('overview');
              setMinute(0);
              setSelection({
                projectionId: entry.projectionId,
                snapshotId: entry.snapshotId,
                title: entry.title,
                acceptedBy: entry.actor,
                returnPage: 'history',
              });
            }}
          />
        )}
      </div>
      <footer className="workspace-footer">
        <span>
          Human-reviewed flow support · Decisions select projected scenarios
        </span>
        {tools}
      </footer>
    </main>
  );
}
