// Colored-cell grid summarizing every watched instrument's bull/bear
// momentum score (backend/watch_scoring.py) -- explicit instruction,
// 2026-10-03: "a grid of colored cells having numbers from the watched
// instruments... show counts for these colors... on click of the color
// cell, filter the watch grid, sort option to sort by these colors, show
// highest at the top." Click toggles both the filter AND the sort (the
// clicked bucket's own relevant score, descending) in one interaction --
// clicking the same cell again clears both back to the unfiltered table.
const BUCKET_ORDER = ['strong_bull', 'mild_bull', 'quiet', 'choppy', 'mild_bear', 'strong_bear']
const BUCKET_LABELS = {
  strong_bull: 'Strong bull', mild_bull: 'Mild bull', quiet: 'Quiet',
  choppy: 'Choppy', mild_bear: 'Mild bear', strong_bear: 'Strong bear',
}

export default function ScoreGrid({ scores, bucketColors, selectedBucket, onSelectBucket }) {
  const counts = Object.fromEntries(BUCKET_ORDER.map((b) => [b, 0]))
  for (const row of scores) counts[row.bucket] = (counts[row.bucket] ?? 0) + 1

  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '12px 0' }}>
      {BUCKET_ORDER.map((bucket) => {
        const isSelected = selectedBucket === bucket
        const color = bucketColors[bucket] ?? '#ccc'
        return (
          <button
            key={bucket}
            type="button"
            onClick={() => onSelectBucket(isSelected ? null : bucket)}
            title={`${BUCKET_LABELS[bucket]} — click to filter + sort by this`}
            style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
              minWidth: 76, padding: '8px 10px', borderRadius: 6, cursor: 'pointer',
              border: isSelected ? '2px solid #222' : '1px solid rgba(0,0,0,0.15)',
              background: color, color: '#fff', textShadow: '0 1px 1px rgba(0,0,0,0.35)',
              fontWeight: isSelected ? 700 : 400,
            }}
          >
            <span style={{ fontSize: 20, lineHeight: 1 }}>{counts[bucket]}</span>
            <span style={{ fontSize: 11 }}>{BUCKET_LABELS[bucket]}</span>
          </button>
        )
      })}
    </div>
  )
}
