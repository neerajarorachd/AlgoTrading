// The name of the watchlist a Workspace tab is showing (2026-10-08, user's
// mockup). Standalone: the page passes the name it resolved.
export default function WatchlistHeading({ name }) {
  return (
    <h2
      data-watchlist-heading
      style={{ margin: '0 0 4px', fontSize: 22, fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--text)', textWrap: 'balance' }}
    >
      {name}
    </h2>
  )
}
