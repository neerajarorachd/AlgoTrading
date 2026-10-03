// Recursive AND/OR condition-tree editor. Tree shape (matches the backend
// exactly — see db/models.py's Strategy*/StrategyCondition* docstrings):
//   { operator: "AND"|"OR", conditions: [...], groups: [...] }
// Plain inline styles, no library — matching this project's existing
// frontend conventions. Visual polish deferred ("design we will decide
// later") — this is a working editor, not a styled one yet.

const OPERATORS = ['>', '>=', '<', '<=', '==', '!=']

// Ready-made formulas a user can drop straight in, one click, to see a
// working example before building their own — explicit instruction,
// 2026-09-29: "UI is not clear what we can do with it." Each string here
// must match the guided term-builder's own generated shape exactly (see
// sideToFormula) so picking one immediately shows correctly in the
// dropdowns underneath, not just as read-only text.
const FORMULA_EXAMPLES = [
  { label: '5-candle avg open is above yesterday-ish close', left: 'mean(open, 5)', operator: '>', right: 'open[-1]' },
  { label: '5-candle avg close above 10-candle avg close', left: 'mean(close, 5)', operator: '>', right: 'mean(close, 10)' },
  { label: 'RSI rising fast (3-candle slope over 45)', left: 'slope(rsi, 3)', operator: '>', right: '45' },
  { label: 'RSI rising faster over 3 candles than over 5', left: 'slope(rsi, 3)', operator: '>', right: 'slope(rsi, 5)' },
]

export function emptyGroup() {
  return { operator: 'AND', conditions: [], groups: [] }
}

function defaultCondition(elements) {
  const first = elements[0]
  const numeric = first?.element_type === 'numeric'
  return {
    element_code: first ? first.code : '',
    operator: numeric ? '>' : null,
    compare_type: numeric ? 'static' : null,
    compared_element_code: null,
    static_value: numeric ? 0 : null,
    static_value_min: null,
    static_value_max: null,
    static_value_step: null,
    left_formula: null,
    right_formula: null,
  }
}

function leafOfType(elements, type) {
  const el = elements.find((e) => e.element_type === type) ?? elements[0]
  return defaultCondition(el ? [el] : [])
}

function updateAtPath(root, path, updater) {
  if (path.length === 0) return updater(root)
  const [head, ...rest] = path
  return {
    ...root,
    groups: root.groups.map((g, i) => (i === head ? updateAtPath(g, rest, updater) : g)),
  }
}

// grammar: {fields, functions, constants} from GET /api/strategy-fields — the
// Formula leaf's pickers, served from the evaluator's own Field enum so the
// UI can never offer something the evaluator rejects.
// leafKinds: which condition types a screen may use (default: all three).
// Recommendation-system rules are ['formula'] — the live rule gate evaluates
// formula leaves only.
const ALL_LEAF_KINDS = ['event', 'numeric', 'formula']

// A formula's default shape: LEFT starts as the field `close`, RIGHT starts
// as the plain number 0 — both real, valid, parseable terms (see
// sideToFormula/parseSideToTerms below), so the dropdown builder always
// opens on something already usable, never on an empty free-text box.
export function blankFormulaLeaf() {
  return {
    ...defaultCondition([]), element_code: null, operator: '>',
    left_formula: sideToFormula([{ op: null, term: { kind: 'field', field: 'close', offset: 0 } }]),
    right_formula: sideToFormula([{ op: null, term: { kind: 'number', value: 0 } }]),
  }
}

// Whole-tree preview — explicit instruction, 2026-10-03: "so that user
// should be able to see what is being cooked." Each formula leaf already
// shows its own "= left op right" line (ConditionEditor below); this
// renders the FULL AND/OR tree (every leaf + nested sub-group) as one
// readable expression, same idea one level up.
function leafToPreviewText(cond) {
  if (cond.left_formula != null) {
    return `${cond.left_formula || '…'} ${cond.operator ?? '>'} ${cond.right_formula || '…'}`
  }
  if (!cond.element_code) return '…'
  if (cond.operator == null) return cond.element_code
  if (cond.compare_type === 'element') return `${cond.element_code} ${cond.operator} ${cond.compared_element_code ?? '…'}`
  if (cond.static_value_min != null) return `${cond.element_code} ${cond.operator} [${cond.static_value_min}–${cond.static_value_max}]`
  return `${cond.element_code} ${cond.operator} ${cond.static_value ?? '…'}`
}

function treeToPreviewText(group) {
  const parts = [
    ...group.conditions.map(leafToPreviewText),
    ...group.groups.map((g) => `(${treeToPreviewText(g)})`),
  ]
  return parts.length ? parts.join(` ${group.operator} `) : '…'
}

export default function StrategyBuilder({ tree, onChange, elements, grammar, leafKinds = ALL_LEAF_KINDS }) {
  function updateGroup(path, updater) {
    onChange(updateAtPath(tree, path, updater))
  }
  return (
    <div>
      <div style={{
        fontFamily: 'monospace', fontSize: 13, color: '#334', background: '#eef6ff',
        border: '1px solid #cfe3fb', borderRadius: 4, padding: '6px 10px', marginBottom: 10,
        wordBreak: 'break-word',
      }}>
        <strong>Preview:</strong> {treeToPreviewText(tree)}
      </div>
      <GroupEditor
        group={tree} path={[]} onUpdateGroup={updateGroup} leafKinds={leafKinds}
        onRemoveGroup={null} elements={elements} grammar={grammar} depth={0}
      />
    </div>
  )
}

function GroupEditor({ group, path, onUpdateGroup, onRemoveGroup, elements, grammar, leafKinds, depth }) {
  const setOperator = (operator) => onUpdateGroup(path, (g) => ({ ...g, operator }))
  const formulaOnly = leafKinds.length === 1 && leafKinds[0] === 'formula'
  const addCondition = () => onUpdateGroup(path, (g) => ({
    ...g, conditions: [...g.conditions, formulaOnly ? blankFormulaLeaf() : defaultCondition(elements)],
  }))
  const updateCondition = (i, cond) => onUpdateGroup(path, (g) => ({
    ...g, conditions: g.conditions.map((c, ci) => (ci === i ? cond : c)),
  }))
  const removeCondition = (i) => onUpdateGroup(path, (g) => ({
    ...g, conditions: g.conditions.filter((_, ci) => ci !== i),
  }))
  const addSubgroup = () => onUpdateGroup(path, (g) => ({ ...g, groups: [...g.groups, emptyGroup()] }))
  const removeSubgroup = (i) => onUpdateGroup(path, (g) => ({
    ...g, groups: g.groups.filter((_, gi) => gi !== i),
  }))

  return (
    <div style={{
      border: '1px solid #ccc', borderRadius: 6, padding: 10, marginBottom: 8,
      background: depth % 2 ? '#f7f8fa' : '#fff',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <select value={group.operator} onChange={(e) => setOperator(e.target.value)}>
          <option value="AND">AND</option>
          <option value="OR">OR</option>
        </select>
        <span style={{ color: '#888', fontSize: 12 }}>
          every item below combined with {group.operator}
        </span>
        {onRemoveGroup && (
          <button type="button" onClick={onRemoveGroup} style={{ marginLeft: 'auto' }}>
            Remove group
          </button>
        )}
      </div>

      {group.conditions.map((cond, i) => (
        <ConditionEditor
          key={i} condition={cond} elements={elements} grammar={grammar} leafKinds={leafKinds}
          onChange={(next) => updateCondition(i, next)}
          onRemove={() => removeCondition(i)}
        />
      ))}

      {group.groups.map((sub, i) => (
        <GroupEditor
          key={i} group={sub} path={[...path, i]} onUpdateGroup={onUpdateGroup}
          onRemoveGroup={() => removeSubgroup(i)} elements={elements} grammar={grammar}
          leafKinds={leafKinds} depth={depth + 1}
        />
      ))}

      <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
        <button type="button" onClick={addCondition}>+ Condition</button>
        <button type="button" onClick={addSubgroup}>+ Sub-group</button>
      </div>
    </div>
  )
}

const leafRowStyle = {
  display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6,
  padding: '6px 0', borderBottom: '1px dashed #e2e2e2',
}

// ----------------------------------------------------------------------
// Formula terms: a formula side (left_formula / right_formula) is built
// ENTIRELY from dropdowns, never typed — explicit instruction, 2026-09-29:
// "dont let the user to type in [the] formula textbox. 90% time it will be
// failed... build formula from dropdowns and textbox [only] to enter a
// fixed value." A side is a sequence of terms joined by +-*/, each term one
// of: a field (with a candle offset), a function call (slope/mean), a named
// constant (e.g. order_size), or a literal number — the ONLY free-text
// entry left is that literal number's own value box.
//
// The string sent to the backend is unchanged (still plain left_formula/
// right_formula text the real evaluator parses) — these two functions are
// a lossless (for anything built here) string <-> term-list conversion, so
// no backend change was needed for this rewrite.

function termToString(t) {
  if (t.kind === 'field') return t.offset ? `${t.field}[${t.offset}]` : t.field
  if (t.kind === 'function') return `${t.fn}(${t.field}, ${t.n})`
  if (t.kind === 'constant') return t.name
  if (t.kind === 'number') return String(t.value)
  return ''
}

function sideToFormula(side) {
  return side.map((entry, i) => (i === 0 ? termToString(entry.term) : ` ${entry.op} ${termToString(entry.term)}`)).join('')
}

function parseTermString(raw, grammar) {
  const s = raw.trim()
  if (!s) return null
  let m = s.match(/^([a-zA-Z_]\w*)\(\s*([a-zA-Z_]\w*)\s*,\s*(-?\d+)\s*\)$/)
  if (m && (grammar?.functions ?? []).some((f) => f.name === m[1])) {
    return { kind: 'function', fn: m[1], field: m[2], n: Number(m[3]) }
  }
  m = s.match(/^([a-zA-Z_]\w*)(?:\[(-?\d+)\])?$/)
  if (m) {
    const name = m[1]
    const offset = m[2] != null ? Number(m[2]) : 0
    if ((grammar?.fields ?? []).some((f) => f.value === name || (f.aliases ?? []).includes(name))) {
      return { kind: 'field', field: name, offset }
    }
    if (offset === 0 && (grammar?.constants ?? []).includes(name)) {
      return { kind: 'constant', name }
    }
    return null
  }
  if (/^-?\d+(\.\d+)?$/.test(s)) return { kind: 'number', value: Number(s) }
  return null
}

// null = this string isn't in the guided shape (typically hand-typed
// before this rewrite existed) — the caller falls back to a read-only view.
function parseSideToTerms(formula, grammar) {
  if (!formula || !formula.trim()) return null
  const parts = formula.split(/\s([+\-*/])\s/)
  if (parts.length % 2 !== 1) return null
  const side = []
  for (let i = 0; i < parts.length; i += 2) {
    const term = parseTermString(parts[i], grammar)
    if (!term) return null
    side.push({ op: i === 0 ? null : parts[i - 1], term })
  }
  return side
}

function TermEditor({ term, grammar, onChange }) {
  const fields = grammar?.fields ?? []
  const functions = grammar?.functions ?? []
  const constants = grammar?.constants ?? []
  const setKind = (kind) => {
    if (kind === 'field') onChange({ kind: 'field', field: fields[0]?.value ?? 'close', offset: 0 })
    else if (kind === 'function') onChange({ kind: 'function', fn: functions[0]?.name ?? 'mean', field: fields[0]?.value ?? 'close', n: 5 })
    else if (kind === 'constant') onChange({ kind: 'constant', name: constants[0] ?? '' })
    else onChange({ kind: 'number', value: 0 })
  }
  return (
    <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
      <select value={term.kind} title="term type" onChange={(e) => setKind(e.target.value)}>
        <option value="field">Field</option>
        <option value="function">Function</option>
        {constants.length > 0 && <option value="constant">Constant</option>}
        <option value="number">Number</option>
      </select>

      {term.kind === 'field' && (
        <>
          <select value={term.field} onChange={(e) => onChange({ ...term, field: e.target.value })}>
            {fields.map((f) => <option key={f.value} value={f.value}>{f.value}</option>)}
          </select>
          <span style={{ color: '#888' }} title="candle offset is always 0 or back in time">(-)</span>
          <input
            type="number" min="0" step="1" title="candles back (0 = current candle)" style={{ width: 50 }}
            value={-term.offset}
            onChange={(e) => {
              const candlesBack = Math.max(0, parseInt(e.target.value, 10) || 0)
              onChange({ ...term, offset: -candlesBack })
            }}
          />
          <span style={{ color: '#888', fontSize: 12 }}>
            {-term.offset === 0 ? 'current candle' : `candle${-term.offset === 1 ? '' : 's'} back`}
          </span>
        </>
      )}

      {term.kind === 'function' && (
        <>
          <select value={term.fn} onChange={(e) => onChange({ ...term, fn: e.target.value })}>
            {functions.map((f) => <option key={f.name} value={f.name} title={f.description}>{f.name}</option>)}
          </select>
          <span>(</span>
          <select value={term.field} onChange={(e) => onChange({ ...term, field: e.target.value })}>
            {fields.map((f) => <option key={f.value} value={f.value}>{f.value}</option>)}
          </select>
          <span>,</span>
          <input
            type="number" min="1" step="1" style={{ width: 50 }} value={term.n}
            onChange={(e) => onChange({ ...term, n: Math.max(1, parseInt(e.target.value, 10) || 1) })}
          />
          <span>)</span>
        </>
      )}

      {term.kind === 'constant' && (
        <select value={term.name} onChange={(e) => onChange({ ...term, name: e.target.value })}>
          {constants.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      )}

      {term.kind === 'number' && (
        <input
          type="number" step="any" style={{ width: 80 }} value={term.value}
          onChange={(e) => onChange({ ...term, value: e.target.value === '' ? 0 : Number(e.target.value) })}
        />
      )}
    </span>
  )
}

function FormulaSide({ formula, grammar, onChangeFormula }) {
  const parsed = parseSideToTerms(formula, grammar)
  // An empty side hasn't been touched yet -- show one default field term
  // without writing anything until the user actually picks something.
  const terms = parsed ?? (formula ? null : [{ op: null, term: { kind: 'field', field: 'close', offset: 0 } }])

  if (terms == null) {
    // Hand-typed before this rewrite (or otherwise outside the guided
    // shape) -- shown read-only rather than guessed at; Rebuild clears it
    // to a fresh, guided default.
    return (
      <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
        <code style={{ background: '#f2f2f2', padding: '2px 6px', borderRadius: 4 }}>{formula}</code>
        <button
          type="button"
          onClick={() => onChangeFormula(sideToFormula([{ op: null, term: { kind: 'field', field: 'close', offset: 0 } }]))}
        >
          Rebuild
        </button>
      </span>
    )
  }

  const updateTerm = (i, nextTerm) => onChangeFormula(sideToFormula(terms.map((e, idx) => (idx === i ? { ...e, term: nextTerm } : e))))
  const updateOp = (i, op) => onChangeFormula(sideToFormula(terms.map((e, idx) => (idx === i ? { ...e, op } : e))))
  const addTerm = () => onChangeFormula(sideToFormula([...terms, { op: '+', term: { kind: 'number', value: 0 } }]))
  const removeTerm = (i) => onChangeFormula(sideToFormula(terms.filter((_, idx) => idx !== i)))

  return (
    <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center', flexWrap: 'wrap' }}>
      {terms.map((entry, i) => (
        <span key={i} style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
          {i > 0 && (
            <select value={entry.op} onChange={(e) => updateOp(i, e.target.value)}>
              {['+', '-', '*', '/'].map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          )}
          <TermEditor term={entry.term} grammar={grammar} onChange={(t) => updateTerm(i, t)} />
          {terms.length > 1 && (
            <button type="button" onClick={() => removeTerm(i)} title="remove this term">×</button>
          )}
        </span>
      ))}
      <button type="button" onClick={addTerm} title="combine with another term">+ term</button>
    </span>
  )
}

// ----------------------------------------------------------------------

function ConditionEditor({ condition, elements, grammar, leafKinds, onChange, onRemove }) {
  const isFormula = condition.left_formula != null
  const element = elements.find((e) => e.code === condition.element_code)
  const isNumeric = element?.element_type === 'numeric'
  const eventElements = elements.filter((e) => e.element_type === 'event')
  const numericElements = elements.filter((e) => e.element_type === 'numeric')
  const mode = condition.compare_type === 'element'
    ? 'element'
    : condition.static_value_min != null ? 'range' : 'static'

  function handleElementChange(code) {
    const next = elements.find((e) => e.code === code)
    if (next?.element_type === 'numeric') {
      onChange({
        ...condition, element_code: code,
        operator: condition.operator || '>', compare_type: condition.compare_type || 'static',
      })
    } else {
      onChange({
        element_code: code, operator: null, compare_type: null, compared_element_code: null,
        static_value: null, static_value_min: null, static_value_max: null, static_value_step: null,
      })
    }
  }

  function handleModeChange(nextMode) {
    if (nextMode === 'element') {
      onChange({
        ...condition, compare_type: 'element', compared_element_code: numericElements[0]?.code ?? null,
        static_value: null, static_value_min: null, static_value_max: null, static_value_step: null,
      })
    } else if (nextMode === 'range') {
      onChange({
        ...condition, compare_type: 'static', compared_element_code: null,
        static_value: null, static_value_min: 0, static_value_max: 100, static_value_step: 1,
      })
    } else {
      onChange({
        ...condition, compare_type: 'static', compared_element_code: null,
        static_value: 0, static_value_min: null, static_value_max: null, static_value_step: null,
      })
    }
  }

  const numField = (label, key, extra) => (
    <input
      type="number" step="any" placeholder={label} title={label}
      value={condition[key] ?? ''}
      onChange={(e) => onChange({ ...condition, [key]: e.target.value === '' ? null : parseFloat(e.target.value) })}
      style={{ width: 64 }}
      {...extra}
    />
  )

  const kind = isFormula ? 'formula' : isNumeric ? 'numeric' : 'event'
  const kindSelect = (
    <select
      value={kind} title="condition type"
      onChange={(e) => {
        const next = e.target.value
        if (next === kind) return
        onChange(next === 'formula' ? blankFormulaLeaf() : leafOfType(elements, next))
      }}
    >
      {leafKinds.includes('event') && <option value="event">Event</option>}
      {leafKinds.includes('numeric') && <option value="numeric">Indicator</option>}
      {leafKinds.includes('formula') && <option value="formula">Formula</option>}
    </select>
  )

  if (isFormula) {
    return (
      <div style={leafRowStyle}>
        {kindSelect}
        <select
          value="" title="start from a ready-made example"
          onChange={(e) => {
            const preset = FORMULA_EXAMPLES[e.target.value]
            if (preset) onChange({ ...condition, left_formula: preset.left, operator: preset.operator, right_formula: preset.right })
          }}
        >
          <option value="">Examples…</option>
          {FORMULA_EXAMPLES.map((ex, i) => <option key={ex.label} value={i}>{ex.label}</option>)}
        </select>
        <FormulaSide
          formula={condition.left_formula ?? ''} grammar={grammar}
          onChangeFormula={(v) => onChange({ ...condition, left_formula: v })}
        />
        <select value={condition.operator ?? '>'} onChange={(e) => onChange({ ...condition, operator: e.target.value })}>
          {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <FormulaSide
          formula={condition.right_formula ?? ''} grammar={grammar}
          onChangeFormula={(v) => onChange({ ...condition, right_formula: v })}
        />
        <button type="button" onClick={onRemove} style={{ marginLeft: 'auto' }} title="Remove condition">×</button>
        <div style={{
          width: '100%', fontFamily: 'monospace', fontSize: 12, color: '#555',
          background: '#f7f8fa', border: '1px solid #e2e2e2', borderRadius: 4, padding: '3px 8px',
        }}>
          = {condition.left_formula || '…'} {condition.operator ?? '>'} {condition.right_formula || '…'}
        </div>
      </div>
    )
  }

  return (
    <div style={leafRowStyle}>
      {kindSelect}
      <select value={condition.element_code} onChange={(e) => handleElementChange(e.target.value)}>
        <optgroup label="Events (fired / not fired)">
          {eventElements.map((e) => <option key={e.code} value={e.code}>{e.code}</option>)}
        </optgroup>
        <optgroup label="Indicators (numeric)">
          {numericElements.map((e) => <option key={e.code} value={e.code}>{e.code}</option>)}
        </optgroup>
      </select>

      {isNumeric && (
        <>
          <select value={condition.operator || '>'} onChange={(e) => onChange({ ...condition, operator: e.target.value })}>
            {OPERATORS.map((op) => <option key={op} value={op}>{op}</option>)}
          </select>

          <select value={mode} onChange={(e) => handleModeChange(e.target.value)}>
            <option value="static">value</option>
            <option value="range">range (backtest sweep)</option>
            <option value="element">another indicator</option>
          </select>

          {mode === 'static' && numField('value', 'static_value')}
          {mode === 'range' && (
            <>
              {numField('min', 'static_value_min')}
              <span>–</span>
              {numField('max', 'static_value_max')}
              <span>step</span>
              {numField('step', 'static_value_step')}
            </>
          )}
          {mode === 'element' && (
            <select
              value={condition.compared_element_code || ''}
              onChange={(e) => onChange({ ...condition, compared_element_code: e.target.value })}
            >
              {numericElements.map((e) => <option key={e.code} value={e.code}>{e.code}</option>)}
            </select>
          )}
        </>
      )}

      <button type="button" onClick={onRemove} style={{ marginLeft: 'auto' }} title="Remove condition">
        ×
      </button>
    </div>
  )
}
