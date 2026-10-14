// Colored-cell grid summarizing every watched instrument's bull/bear
// momentum score (backend/watch_scoring.py) -- explicit instruction,
// 2026-10-03: "a grid of colored cells having numbers from the watched
// instruments... show counts for these colors... on click of the color
// cell, filter the watch grid, sort option to sort by these colors, show
// highest at the top." Click toggles both the filter AND the sort (the
// clicked bucket's own relevant score, descending) in one interaction --
// clicking the same cell again clears both back to the unfiltered table.
// Pattern colours (ALGO-33), top to bottom -- the order is the colour value
// (dark green 6 ... red 0), so "Sort: status color" puts green on top and
// red at the bottom.
export const BUCKET_ORDER = ['dark_green', 'light_green', 'orange', 'yellow', 'white', 'pink', 'red']
const BUCKET_LABELS = {
  dark_green: 'Dark green: strong setup', light_green: 'Light green: good setup', orange: 'Orange: fair setup',
  yellow: 'Yellow: weak setup', white: 'White: nothing formed', pink: 'Pink: caution -- avoid', red: 'Red: avoid',
}

// countsOverride (bucket -> number): show these instead of "instruments in
// each bucket" -- the replay feed's placeholder pattern counts (2026-10-07:
// "we will decide later how the color is related to the patterns, just add
// pattern counts randomly on the color cards"). Click still filters by the
// instruments' real buckets.
const TF_ORDER = ['1min', '3min', '5min']
// light tiles need dark digits to stay readable
const DARK_TEXT = new Set(['white', 'yellow'])

// timeframe 'all': rows carry by_timeframe, and each tile shows its count per
// timeframe ("2 · 1 · 0" = 1m · 3m · 5m); a click still filters on the row's
// combined colour.
export default function ScoreGrid({ scores, bucketColors, selectedBucket, onSelectBucket, countsOverride, overrideNote, timeframe }) {
  const counts = Object.fromEntries(BUCKET_ORDER.map((b) => [b, 0]))
  for (const row of scores) counts[row.bucket] = (counts[row.bucket] ?? 0) + 1
  if (countsOverride) Object.assign(counts, countsOverride)
  const perTf = timeframe === 'all' && !countsOverride
    ? Object.fromEntries(BUCKET_ORDER.map((b) => [b, TF_ORDER.map((tf) => scores.filter((r) => r.by_timeframe?.[tf]?.colour === b).length)]))
    : null

  return (
    <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap', marginBottom: 'var(--space-3)' }}>
      {BUCKET_ORDER.map((bucket) => {
        const isSelected = selectedBucket === bucket
        const color = bucketColors[bucket] ?? 'var(--text-faint)'
        return (
          <button
            key={bucket}
            type="button"
            onClick={() => onSelectBucket(isSelected ? null : bucket)}
            data-bucket={bucket}
            title={`${BUCKET_LABELS[bucket]}${perTf ? ` — 1m: ${perTf[bucket][0]}, 3m: ${perTf[bucket][1]}, 5m: ${perTf[bucket][2]}` : ''} — click to filter + sort by this${overrideNote ? ` (${overrideNote})` : ''}`}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              minWidth: 42, padding: '5px 10px', borderRadius: 'var(--radius-sm)',
              border: isSelected ? '2px solid var(--text)' : '1px solid rgba(0,0,0,0.12)',
              boxShadow: isSelected ? 'var(--shadow-md)' : 'var(--shadow-sm)',
              background: color,
              color: DARK_TEXT.has(bucket) ? '#2a2733' : '#fff',
              textShadow: DARK_TEXT.has(bucket) ? 'none' : '0 1px 1px rgba(0,0,0,0.35)',
              fontWeight: isSelected ? 700 : 500,
              transform: isSelected ? 'translateY(-1px)' : undefined,
              transition: 'transform 0.1s ease, box-shadow 0.1s ease',
            }}
          >
            {perTf ? (
              <span className="num" data-tf-counts style={{ fontSize: 13, lineHeight: 1.2, fontWeight: 700, whiteSpace: 'nowrap' }}>
                {perTf[bucket].join(' · ')}
              </span>
            ) : (
              <span className="num" style={{ fontSize: 15, lineHeight: 1.2, fontWeight: 700 }}>{counts[bucket]}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}
