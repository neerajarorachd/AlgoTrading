// Recursive AND/OR condition-tree editor. Tree shape (matches the backend
// exactly — see db/models.py's Strategy*/StrategyCondition* docstrings):
//   { operator: "AND"|"OR", conditions: [...], groups: [...] }
// Plain inline styles, no library — matching this project's existing
// frontend conventions. Visual polish deferred ("design we will decide
// later") — this is a working editor, not a styled one yet.

const OPERATORS = ['>', '>=', '<', '<=', '==', '!=']

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

export function blankFormulaLeaf() {
  return { ...defaultCondition([]), element_code: null, operator: '>', left_formula: '', right_formula: '' }
}

export default function StrategyBuilder({ tree, onChange, elements, grammar, leafKinds = ALL_LEAF_KINDS }) {
  function updateGroup(path, updater) {
    onChange(updateAtPath(tree, path, updater))
  }
  return (
    <GroupEditor
      group={tree} path={[]} onUpdateGroup={updateGroup} leafKinds={leafKinds}
      onRemoveGroup={null} elements={elements} grammar={grammar} depth={0}
    />
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

// A formula is a free-text expression (e.g. mean(volume, 5) > order_size)
// checked server-side by dry-running the real evaluator; the two pickers
// just insert names from the served grammar so nobody has to remember them.
function FormulaInput({ value, onChange, grammar, width }) {
  const insert = (text) => {
    if (!text) return
    onChange(value && !/[\s(,]$/.test(value) ? `${value} ${text}` : `${value}${text}`)
  }
  return (
    <span style={{ display: 'inline-flex', gap: 2 }}>
      <input
        value={value} spellCheck={false} placeholder="e.g. mean(volume, 5)"
        onChange={(e) => onChange(e.target.value)} style={{ width }}
      />
      <select value="" title="insert a field" onChange={(e) => insert(e.target.value)}>
        <option value="">+ field</option>
        <optgroup label="Fields">
          {(grammar?.fields ?? []).map((f) => <option key={f.value} value={f.value}>{f.value}</option>)}
        </optgroup>
        <optgroup label="Constants">
          {(grammar?.constants ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
        </optgroup>
      </select>
      <select value="" title="insert a function" onChange={(e) => insert(e.target.value)}>
        <option value="">+ fn</option>
        {(grammar?.functions ?? []).map((f) => (
          <option key={f.name} value={`${f.name}(`} title={f.description}>{f.signature}</option>
        ))}
      </select>
    </span>
  )
}

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
    const op = condition.operator
    return (
      <div style={leafRowStyle}>
        {kindSelect}
        <FormulaInput
          value={condition.left_formula} grammar={grammar} width={220}
          onChange={(v) => onChange({ ...condition, left_formula: v })}
        />
        <select
          value={op ?? ''}
          onChange={(e) => {
            const next = e.target.value || null
            onChange({ ...condition, operator: next, right_formula: next ? (condition.right_formula ?? '') : null })
          }}
        >
          <option value="">(formula is itself a comparison)</option>
          {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        {op != null && (
          <FormulaInput
            value={condition.right_formula ?? ''} grammar={grammar} width={140}
            onChange={(v) => onChange({ ...condition, right_formula: v })}
          />
        )}
        <button type="button" onClick={onRemove} style={{ marginLeft: 'auto' }} title="Remove condition">×</button>
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
