// Colored-cell grid summarizing every watched instrument's bull/bear
// momentum score (backend/watch_scoring.py) -- explicit instruction,
// 2026-10-03: "a grid of colored cells having numbers from the watched
// instruments... show counts for these colors... on click of the color
// cell, filter the watch grid, sort option to sort by these colors, show
// highest at the top." Click toggles both the filter AND the sort (the
// clicked bucket's own relevant score, descending) in one interaction --
// clicking the same cell again clears both back to the unfiltered table.
export const BUCKET_ORDER = ['strong_bull', 'mild_bull', 'quiet', 'choppy', 'mild_bear', 'strong_bear']
const BUCKET_LABELS = {
  strong_bull: 'Strong bull', mild_bull: 'Mild bull', quiet: 'Quiet',
  choppy: 'Choppy', mild_bear: 'Mild bear', strong_bear: 'Strong bear',
}

// countsOverride (bucket -> number): show these instead of "instruments in
// each bucket" -- the replay feed's placeholder pattern counts (2026-10-07:
// "we will decide later how the color is related to the patterns, just add
// pattern counts randomly on the color cards"). Click still filters by the
// instruments' real buckets.
export default function ScoreGrid({ scores, bucketColors, selectedBucket, onSelectBucket, countsOverride, overrideNote }) {
  const counts = Object.fromEntries(BUCKET_ORDER.map((b) => [b, 0]))
  for (const row of scores) counts[row.bucket] = (counts[row.bucket] ?? 0) + 1
  if (countsOverride) Object.assign(counts, countsOverride)

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
            title={`${BUCKET_LABELS[bucket]} — click to filter + sort by this${overrideNote ? ` (${overrideNote})` : ''}`}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              minWidth: 42, padding: '5px 10px', borderRadius: 'var(--radius-sm)',
              border: isSelected ? '2px solid var(--text)' : '1px solid rgba(0,0,0,0.12)',
              boxShadow: isSelected ? 'var(--shadow-md)' : 'var(--shadow-sm)',
              background: color, color: '#fff', textShadow: '0 1px 1px rgba(0,0,0,0.35)',
              fontWeight: isSelected ? 700 : 500,
              transform: isSelected ? 'translateY(-1px)' : undefined,
              transition: 'transform 0.1s ease, box-shadow 0.1s ease',
            }}
          >
            <span className="num" style={{ fontSize: 15, lineHeight: 1.2, fontWeight: 700 }}>{counts[bucket]}</span>
          </button>
        )
      })}
    </div>
  )
}
