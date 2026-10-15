// Order of rows within one colour (2026-10-15): the most timeframes green
// first -- all three of 1m/3m/5m, then two, then one, then none -- then more
// dark greens, then the strongest top pattern (ALGO-33). Rows are
// /api/watch-scores rows (each carries by_timeframe). Negative = `a` first.
const TIMEFRAMES = ['1min', '3min', '5min']
const GREENS = new Set(['dark_green', 'light_green'])
const DARK_GREEN = new Set(['dark_green'])

export const countTimeframes = (row, colours) => TIMEFRAMES.filter((tf) => colours.has(row?.by_timeframe?.[tf]?.colour)).length
export const greenCount = (row) => countTimeframes(row, GREENS)

export function withinColour(a, b) {
  return (countTimeframes(b, GREENS) - countTimeframes(a, GREENS))
    || (countTimeframes(b, DARK_GREEN) - countTimeframes(a, DARK_GREEN))
    || ((b?.top_score ?? 0) - (a?.top_score ?? 0))
}
