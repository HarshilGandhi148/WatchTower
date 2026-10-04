import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { MissionControl } from './components/exchange/MissionControl';
import './styles.css';
import { createDashboardClient } from './lib/connection';
import { createSpacetimeTransport } from './lib/spacetimeTransport';

const client = createDashboardClient(createSpacetimeTransport());

const root = document.getElementById('root');
if (!root) {
  throw new Error('Missing application root');
}

createRoot(root).render(
  <StrictMode>
    <MissionControl client={client} />
  </StrictMode>,
);
