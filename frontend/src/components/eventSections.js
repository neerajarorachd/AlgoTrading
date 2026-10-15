// Event list order (ALGO-34), shared by the overall live list and each row's
// list: the screen's timeframe in its last n candles first, then the other
// timeframes in their last n candles, then everything older. "Last n candles"
// counts back from the newest event, so it reads the same after hours and on
// a replay. On "All" the first two sections are one.
export const RECENT_CANDLES = 10 // the pattern lookback

const TF_MINUTES = { '1min': 1, '3min': 3, '5min': 5 }
const label = (tf) => tf.replace('min', 'm')

export function eventSections(events, timeframe, n = RECENT_CANDLES) {
  if (!events?.length) return []
  const at = (e) => Date.parse(e.ts)
  const newest = Math.max(...events.map(at))
  const isRecent = (e) => newest - at(e) < n * (TF_MINUTES[e.timeframe] ?? 1) * 60000
  const byNewest = (a, b) => at(b) - at(a)
  const single = timeframe in TF_MINUTES
  const sections = single
    ? [
        { key: 'selected', label: `${label(timeframe)} · last ${n} candles`, rows: events.filter((e) => isRecent(e) && e.timeframe === timeframe) },
        { key: 'recent', label: `Other timeframes · last ${n} candles`, rows: events.filter((e) => isRecent(e) && e.timeframe !== timeframe) },
      ]
    : [{ key: 'recent', label: `All timeframes · last ${n} candles`, rows: events.filter(isRecent) }]
  sections.push({ key: 'older', label: 'Earlier', rows: events.filter((e) => !isRecent(e)) })
  return sections.filter((s) => s.rows.length).map((s) => ({ ...s, rows: [...s.rows].sort(byNewest) }))
}
