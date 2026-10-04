import { useEffect, useRef, useState } from 'react';
import type { Advisory, DashboardSnapshot } from '../../features/live/types';
import {
  decisionBlockReason,
  validParameter,
} from '../../features/advisories/policy';
import type { DashboardClient, CommandInput } from '../../lib/connection';

export function AdvisoryDecision({
  advisory,
  state,
  client,
  now,
  projectionReady,
  onApproveRecorded,
}: {
  advisory: Advisory;
  state: DashboardSnapshot;
  client: DashboardClient;
  now: number;
  projectionReady: boolean;
  onApproveRecorded: (advisory: Advisory) => void;
}) {
  const inFlight = useRef(false);
  const editor = useRef<HTMLFormElement>(null);
  const [editing, setEditing] = useState(false);
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
  const projectionBlocked = Boolean(advisory.projectionId && !projectionReady);
  const disabled = Boolean(blocked) || pending || acknowledged;
  const valid =
    advisory.parameters.length > 0 &&
    advisory.parameters.every(
      (p) => values[p.key]?.trim() && validParameter(p, Number(values[p.key])),
    ) &&
    Boolean(rationale.trim());
  async function submit(input: CommandInput) {
    if (
      disabled ||
      inFlight.current ||
      (input.kind === 'decision' &&
        input.decision === 'approve' &&
        projectionBlocked)
    )
      return;
    inFlight.current = true;
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
      inFlight.current = false;
      setPending(false);
    }
  }
  const base = {
    advisoryId: advisory.id,
    version: advisory.version,
    snapshotId: advisory.snapshotId,
  };
  useEffect(() => {
    if (editing)
      editor.current?.querySelector<HTMLInputElement>('input')?.focus();
  }, [editing]);
  return (
    <footer className={`decision-footer ${editing ? 'editing-decision' : ''}`}>
      <div className="decision-actions">
        <button
          className="button-primary"
          disabled={disabled || projectionBlocked}
          onClick={() =>
            void submit({ ...base, kind: 'decision', decision: 'approve' })
          }
        >
          Approve scenario
        </button>
        <button
          disabled={disabled || !advisory.parameters.length}
          aria-expanded={editing}
          onClick={() => setEditing(!editing)}
        >
          Edit
        </button>
        <button
          className="button-quiet"
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
      {blocked && <p className="decision-message">{blocked}</p>}
      {!blocked && projectionBlocked && (
        <p className="decision-message">
          Validated projection unavailable for this version.
        </p>
      )}
      {pending && <p role="status">Submitting request…</p>}
      {message && <p role="status">{message}</p>}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {editing && (
        <form
          ref={editor}
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
    </footer>
  );
}
