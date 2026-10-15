// Unit test for the colour sort's within-colour order (2026-10-15).
// Run: node unit_colour_sort.test.mjs
import assert from 'node:assert/strict'
import { withinColour, greenCount } from './src/components/colourSort.js'

const row = (id, cols, top = 0) => ({ id, top_score: top, by_timeframe: Object.fromEntries(['1min', '3min', '5min'].map((tf, i) => [tf, { colour: cols[i] }])) })
const rows = [
  row('one-green', ['light_green', 'white', 'orange'], 4),
  row('none', ['white', 'yellow', 'orange'], 9),
  row('three-green-light', ['light_green', 'light_green', 'light_green'], 4),
  row('two-green', ['dark_green', 'white', 'light_green'], 5),
  row('three-green-dark', ['dark_green', 'dark_green', 'light_green'], 5),
  row('two-green-weaker', ['dark_green', 'light_green', 'white'], 3),
  row('two-green-same-darks-stronger', ['dark_green', 'light_green', 'white'], 4.5),
]
const order = [...rows].sort(withinColour).map((r) => r.id)
assert.deepEqual(order, [
  'three-green-dark',            // 3 greens, 2 dark
  'three-green-light',           // 3 greens, 0 dark
  'two-green',                   // 2 greens, 1 dark, top 5
  'two-green-same-darks-stronger', // 2 greens, 1 dark, top 4.5
  'two-green-weaker',            // 2 greens, 1 dark, top 3
  'one-green',
  'none',                        // most score, but no green: last
])
assert.equal(greenCount(row('x', ['pink', 'red', 'dark_green'])), 1)
assert.equal(greenCount({}), 0) // a row with no by_timeframe counts 0, no error
console.log('colour sort order ok:', order.join(' > '))
