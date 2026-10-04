import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import App from '../src/App';
import { Popover } from '../src/components/workspace/Popover';
import { createDashboardClient } from '../src/lib/connection';
import { createTestServer } from '../src/test/TestServer';
import '../src/styles.css';

// This separate entrypoint never shares sample observations with the live app.
const server = createTestServer();
const controllerA = createDashboardClient(
  server.transport('test-controller-a'),
  'TEST',
);
const controllerB = createDashboardClient(
  server.transport('test-controller-b'),
  'TEST',
);
const viewer = createDashboardClient(server.transport('test-viewer'), 'TEST');
export default function TestPreview() {
  const [secondSession, setSecondSession] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  return (
    <App
      key={readOnly ? 'viewer' : secondSession ? 'second' : 'primary'}
      client={readOnly ? viewer : secondSession ? controllerB : controllerA}
      tools={
        <Popover className="preview-settings" summary="Demo settings">
          <p>Local sample data only. Rehearse review and connection states.</p>
          <div
            className="preview-controls"
            aria-label="Demo rehearsal controls"
          >
            <button onClick={() => setSecondSession(!secondSession)}>
              Switch to {secondSession ? 'primary' : 'second'} session
            </button>
            <label>
              <input
                type="checkbox"
                checked={readOnly}
                onChange={(event) => setReadOnly(event.target.checked)}
              />
              Read-only view
            </label>
            <button onClick={() => server.disconnect()}>Disconnect</button>
            <button
              onClick={() => {
                server.synchronizing();
                window.setTimeout(() => server.synchronize(), 500);
              }}
            >
              Reconnect
            </button>
            <button onClick={() => server.revalidate()}>
              Revalidate draft
            </button>
            <button
              onClick={() =>
                server.setData({
                  ...server.getData(),
                  nearbyCount: null,
                  aircraft: [],
                  sources: server.getData().sources.map((source) => ({
                    ...source,
                    status: 'unavailable',
                    detail: 'Simulated source outage. Metrics unavailable.',
                  })),
                })
              }
            >
              Simulate feed outage
            </button>
            <button onClick={() => window.location.reload()}>
              Reset sample data
            </button>
          </div>
        </Popover>
      }
    />
  );
}
const root = document.getElementById('root');
if (root) createRoot(root).render(<TestPreview />);
