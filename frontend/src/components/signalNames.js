// Display names for signal codes (2026-11-09). Codes stay as stored; only
// what the user reads changes. Unlisted codes: underscores to spaces.
const NAMES = {
  bullish_structure_shift: 'LL-HH-HL (bullish shift)',
  bearish_structure_shift: 'HH-LL-LH (bearish shift)',
  bullish_break_of_structure: 'HL-HH (bullish continuation)',
  bearish_break_of_structure: 'LH-LL (bearish continuation)',
  swing_hh: 'Swing HH',
  swing_hl: 'Swing HL',
  swing_lh: 'Swing LH',
  swing_ll: 'Swing LL',
  swing_eh: 'Swing EH',
  swing_el: 'Swing EL',
}

/** Name for a signal code; `capitalize` upper-cases the first letter of unlisted codes. */
export function signalName(code, { capitalize = true } = {}) {
  const s = String(code)
  if (NAMES[s]) return NAMES[s]
  const plain = s.replace(/_/g, ' ')
  return capitalize ? plain.replace(/^./, (c) => c.toUpperCase()) : plain
}
