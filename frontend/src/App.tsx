export default function App() {
  return (
    <main className="dashboard">
      <header>
        <p className="eyebrow">WatchTower · KDTW</p>
        <h1>DTW flow advisory</h1>
      </header>
      <section aria-labelledby="connection-title">
        <h2 id="connection-title">Connection unavailable</h2>
        <p role="status">No live observations have been received.</p>
        <p>Advisories and controller decisions are unavailable.</p>
      </section>
    </main>
  );
}
