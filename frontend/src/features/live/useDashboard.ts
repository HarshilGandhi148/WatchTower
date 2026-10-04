import { useEffect, useState, useSyncExternalStore } from 'react';
import type { DashboardClient } from '../../lib/connection';

export function useDashboard(client: DashboardClient) {
  const snapshot = useSyncExternalStore(client.subscribe, client.getSnapshot);
  useEffect(() => client.start(), [client]);
  return snapshot;
}
export function useClock() {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}
