import { useId, useState } from 'react';
import type { Advisory, DashboardSnapshot } from '../../features/live/types';
import {
  decisionBlockReason,
  validParameter,
} from '../../features/advisories/policy';
import type { DashboardClient, CommandInput } from '../../lib/connection';
import { ageLabel, formatTime } from '../../lib/time';

export function AdvisoryCard({
  advisory,
  state,
  client,
  now,
  timezone,
  onPreview,
  onApproveRecorded,
  compact = false,
  initialEditing = false,
  onReview,
  onEdit,
}: {
  advisory: Advisory;
  state: DashboardSnapshot;
  client: DashboardClient;
  now: number;
  timezone: string;
  onPreview: (advisory: Advisory) => void;
  onApproveRecorded: (advisory: Advisory) => void;
  compact?: boolean;
  initialEditing?: boolean;
  onReview?: (advisory: Advisory) => void;
  onEdit?: (advisory: Advisory) => void;
}) {
  const heading = useId();
  const [editing, setEditing] = useState(initialEditing);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      advisory.parameters.map((p) => [p.key, String(p.value)]),
    ),
  );
  const [rationale, setRationale] = useState('');
  const [pending, setPending] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const blocked = decisionBlockReason(state, advisory, now);
  const disabled = Boolean(blocked) || pending || acknowledged;
  const valid =
    advisory.parameters.length > 0 &&
    advisory.parameters.every(
      (p) => values[p.key]?.trim() && validParameter(p, Number(values[p.key])),
    ) &&
    Boolean(rationale.trim());
  async function submit(input: CommandInput) {
    if (disabled) return;
    setPending(true);
    setError('');
    setMessage('');
    try {
      const ack = await client.submit(input);
      if (input.kind === 'decision' && input.decision === 'approve')
        onApproveRecorded(advisory);
      setMessage(ack.message);
      setAcknowledged(true);
      setEditing(false);
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : 'Request failed. Review the latest state before retrying.',
      );
    } finally {
      setPending(false);
    }
  }
  const base = {
    advisoryId: advisory.id,
    version: advisory.version,
    snapshotId: advisory.snapshotId,
  };
  return (
    <article
      className={`advisory-card priority-${advisory.priority} ${compact ? 'compact-advisory' : ''}`}
      aria-labelledby={heading}
    >
      <div className="card-meta">
        <span className="priority-label">
          {advisory.priority.replaceAll('_', ' ')}
        </span>
        <span>
          v{advisory.version} · {advisory.quality} evidence
        </span>
      </div>
      <h3 id={heading}>{advisory.title}</h3>
      <p className="response">{advisory.response}</p>
      <p>{advisory.reason}</p>
      <dl className="card-facts">
        <div>
          <dt>Resource</dt>
          <dd>{advisory.resource}</dd>
        </div>
        <div>
          <dt>Owner</dt>
          <dd>{advisory.ownerRole}</dd>
        </div>
        <div>
          <dt>Window</dt>
          <dd>
            {formatTime(
              advisory.windowStartUtc,
              timezone,
              compact ? 'clock' : 'full',
            )}{' '}
            –{' '}
            {formatTime(
              advisory.windowEndUtc,
              timezone,
              compact ? 'clock' : 'full',
            )}
          </dd>
        </div>
        <div>
          <dt>Expires</dt>
          <dd>
            {formatTime(
              advisory.expiresAtUtc,
              timezone,
              compact ? 'clock' : 'full',
            )}
          </dd>
        </div>
      </dl>
      {compact ? (
        <button className="review-details" onClick={() => onReview?.(advisory)}>
          Review details
        </button>
      ) : (
        <details open>
          <summary>Evidence, assumptions and limitations</summary>
          <ul className="plain-list">
            {advisory.evidence.map((e) => (
              <li key={e.id}>
                <strong>{e.source}</strong> · {e.summary}
                <br />
                <small>
                  {e.id} · {formatTime(e.atUtc, timezone)} ·{' '}
                  {ageLabel(e.atUtc, now)}
                </small>
              </li>
            ))}
          </ul>
          <h4>Assumptions</h4>
          <ul>
            {advisory.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
          <h4>Limitations</h4>
          <ul>
            {advisory.limitations.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
          <small>Evidence snapshot: {advisory.snapshotId}</small>
        </details>
      )}
      <div className="card-actions">
        <button
          onClick={() => onPreview(advisory)}
          disabled={!advisory.projectionId}
        >
          Preview
        </button>
        <button
          className="button-primary"
          disabled={disabled}
          onClick={() =>
            void submit({ ...base, kind: 'decision', decision: 'approve' })
          }
        >
          Approve
        </button>
        <button
          disabled={disabled || !advisory.parameters.length}
          onClick={() =>
            compact && onEdit ? onEdit(advisory) : setEditing(!editing)
          }
        >
          Edit
        </button>
        <button
          disabled={disabled}
          onClick={() =>
            void submit({ ...base, kind: 'decision', decision: 'ignore' })
          }
        >
          Ignore
        </button>
      </div>
      <small className="approval-explanation">
        Record decision and select projected scenario.
      </small>
      {!advisory.projectionId && (
        <p className="muted">Projection not estimable with available data.</p>
      )}
      {blocked && <p className="muted">{blocked}</p>}
      {pending && <p role="status">Submitting request…</p>}
      {message && <p role="status">{message}</p>}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {editing && (
        <form
          className="edit-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid)
              void submit({
                ...base,
                kind: 'edit',
                parameters: Object.fromEntries(
                  advisory.parameters.map((p) => [
                    p.key,
                    Number(values[p.key]),
                  ]),
                ),
                rationale: rationale.trim(),
              });
          }}
        >
          <h4>Propose a revised version</h4>
          <p>
            Changes require validation and a new projection before approval.
          </p>
          {advisory.parameters.map((p) => (
            <label key={p.key}>
              {p.label} ({p.unit})
              <input
                type="number"
                required
                value={values[p.key] ?? ''}
                min={p.min}
                max={p.max}
                step={p.step}
                disabled={disabled}
                onChange={(event) =>
                  setValues({ ...values, [p.key]: event.target.value })
                }
              />
              <small>
                Allowed: {p.min}–{p.max}, step {p.step} {p.unit}
              </small>
            </label>
          ))}
          <label>
            Rationale
            <textarea
              required
              value={rationale}
              disabled={disabled}
              onChange={(event) => setRationale(event.target.value)}
            />
          </label>
          <div className="card-actions">
            <button
              className="button-primary"
              type="submit"
              disabled={disabled || !valid}
            >
              Submit for revalidation
            </button>
            <button
              type="button"
              disabled={pending}
              onClick={() => setEditing(false)}
            >
              Cancel edit
            </button>
          </div>
        </form>
      )}
    </article>
  );
}
