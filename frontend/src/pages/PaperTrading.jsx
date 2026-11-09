import { useEffect, useState } from 'react'
import {
  createPaperBacktest, deletePaperSelection, getPaperBacktest, listPaperBacktests, listPaperLiveTrades,
  listPaperSelections, listPatterns, listSymbols, savePaperSelection,
} from '../api/client.js'
import { usePreference } from '../preferences.js'

// Paper trading (rulebook 3a). Live: pick patterns, set stop/target/lot per
// pattern, see every hit (taken or not). Backtest: one comparison (master)
// with several runs, each with its own patterns and stop/target/lot.
// No broker call anywhere.

export const DEFAULT_SETTINGS = {
  stop_mode: 'atr', stop_value: 2, target_mode: 'atr', target_value: 10,
  atr_period: 14, atr_timeframe: 'hit',
  size_mode: 'amount', amount_per_trade: 100000, quantity_per_trade: 1,
  entry_from: 930, entry_until: 1445, day_end: 1510, slippage_pct: 0.05,
}
const MODE_LABEL = { atr: '× ATR', pct: '% of entry', points: '₹ points' }

const S = {
  page: { display: 'flex', flexDirection: 'column', gap: 16, padding: 16, maxWidth: 1280 },
  card: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 16,
    display: 'flex', flexDirection: 'column', gap: 12 },
  row: { display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' },
  field: { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-dim)' },
  input: { padding: '6px 8px', border: '1px solid var(--border-strong)', borderRadius: 6,
    background: 'var(--surface-alt)', color: 'var(--text)', minWidth: 0 },
  btn: { padding: '7px 14px', borderRadius: 6, border: 'none', background: 'var(--accent)',
    color: 'var(--text-on-accent)', cursor: 'pointer', fontWeight: 600 },
  btnGhost: { padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border-strong)',
    background: 'transparent', color: 'var(--text)', cursor: 'pointer' },
  tab: (on) => ({ padding: '8px 18px', borderRadius: 8, border: '1px solid var(--border)', cursor: 'pointer',
    fontWeight: 600, background: on ? 'var(--accent)' : 'var(--surface)',
    color: on ? 'var(--text-on-accent)' : 'var(--text)' }),
  chip: (on) => ({ padding: '4px 10px', borderRadius: 14, cursor: 'pointer', fontSize: 12,
    border: `1px solid ${on ? 'var(--accent)' : 'var(--border)'}`,
    background: on ? 'var(--accent-soft)' : 'transparent', color: on ? 'var(--accent-text)' : 'var(--text-dim)' }),
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: { textAlign: 'left', padding: '6px 8px', borderBottom: '1px solid var(--border)', color: 'var(--text-dim)',
    fontWeight: 600, whiteSpace: 'nowrap' },
  td: { padding: '6px 8px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' },
  error: { color: 'var(--critical)', background: 'var(--critical-soft)', padding: 8, borderRadius: 6 },
  h3: { margin: 0, fontSize: 15 },
  dim: { color: 'var(--text-dim)', fontSize: 12 },
}

function Num({ label, value, onChange, step = 'any', width = 90 }) {
  return (
    <label style={S.field}>{label}
      <input type="number" step={step} value={value} style={{ ...S.input, width }}
        onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))} />
    </label>
  )
}

function ModeSelect({ label, value, onChange, options }) {
  return (
    <label style={S.field}>{label}
      <select value={value} style={S.input} onChange={(e) => onChange(e.target.value)}>
        {Object.entries(options).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
    </label>
  )
}

/** Stop / target / lot editor, used by live selections and backtest runs. */
export function SettingsEditor({ value, onChange }) {
  const set = (k) => (v) => onChange({ ...value, [k]: v })
  return (
    <div style={S.row} data-testid="paper-settings">
      <ModeSelect label="Stop" value={value.stop_mode} onChange={set('stop_mode')} options={MODE_LABEL} />
      <Num label="Stop value" value={value.stop_value} onChange={set('stop_value')} />
      <ModeSelect label="Target" value={value.target_mode} onChange={set('target_mode')} options={MODE_LABEL} />
      <Num label="Target value" value={value.target_value} onChange={set('target_value')} />
      {(value.stop_mode === 'atr' || value.target_mode === 'atr') && (<>
        <Num label="ATR period" step={1} value={value.atr_period} onChange={set('atr_period')} width={70} />
        <ModeSelect label="ATR timeframe" value={value.atr_timeframe} onChange={set('atr_timeframe')}
          options={{ hit: "Hit's timeframe", '1min': '1 min', '3min': '3 min', '5min': '5 min' }} />
      </>)}
      <ModeSelect label="Size" value={value.size_mode} onChange={set('size_mode')}
        options={{ amount: '₹ amount', qty: 'Lot / qty' }} />
      {value.size_mode === 'qty'
        ? <Num label="Quantity" step={1} value={value.quantity_per_trade} onChange={set('quantity_per_trade')} />
        : <Num label="Amount ₹" step={1000} width={110} value={value.amount_per_trade} onChange={set('amount_per_trade')} />}
      <Num label="Entry from (HHMM)" step={1} value={value.entry_from} onChange={set('entry_from')} />
      <Num label="Entry until" step={1} value={value.entry_until} onChange={set('entry_until')} />
      <Num label="Day end" step={1} value={value.day_end} onChange={set('day_end')} />
      <Num label="Slippage %" value={value.slippage_pct} onChange={set('slippage_pct')} width={70} />
    </div>
  )
}

const pickSettings = (row) => Object.fromEntries(Object.keys(DEFAULT_SETTINGS).map((k) => [k, row[k]]))
const fmt = (v, d = 2) => (v === null || v === undefined ? '—' : Number(v).toFixed(d))
const istTime = (iso) => (iso ? new Date(iso).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata',
  day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }) : '—')
const pnlColor = (v) => (v > 0 ? 'var(--up)' : v < 0 ? 'var(--down)' : 'inherit')

function TradesTable({ trades }) {
  if (!trades.length) return <div style={S.dim}>No hits recorded yet.</div>
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={S.table} data-testid="paper-trades">
        <thead><tr>{['Hit', 'Stock', 'TF', 'Pattern', 'Side', 'Status', 'Entry', 'Qty', 'Stop', 'Target',
          'Exit', 'Reason', 'Net ₹', 'Net %'].map((h) => <th key={h} style={S.th}>{h}</th>)}</tr></thead>
        <tbody>{trades.map((t) => (
          <tr key={t.id}>
            <td style={S.td}>{istTime(t.hit_ts)}</td><td style={S.td}>{t.symbol}</td>
            <td style={S.td}>{t.timeframe}</td><td style={S.td}>{t.item_key}</td>
            <td style={{ ...S.td, color: t.direction === 'buy' ? 'var(--up)' : 'var(--down)' }}>
              {t.direction === 'buy' ? '▲ Buy' : '▼ Sell'}</td>
            <td style={S.td} title={t.not_taken_reason || ''}>
              {t.status === 'not_taken' ? `not taken: ${t.not_taken_reason}` : t.status}</td>
            <td style={S.td}>{fmt(t.entry_price)}</td><td style={S.td}>{t.quantity ?? '—'}</td>
            <td style={S.td}>{fmt(t.stop_price)}</td><td style={S.td}>{fmt(t.target_price)}</td>
            <td style={S.td}>{fmt(t.exit_price)}</td><td style={S.td}>{t.exit_reason || '—'}</td>
            <td style={{ ...S.td, color: pnlColor(t.net_pnl) }}>{fmt(t.net_pnl)}</td>
            <td style={{ ...S.td, color: pnlColor(t.net_pct) }}>{fmt(t.net_pct, 3)}</td>
          </tr>))}</tbody>
      </table>
    </div>
  )
}

function LiveTab({ patterns, defaults, setError }) {
  const [selections, setSelections] = useState([])
  const [drafts, setDrafts] = useState({})
  const [trades, setTrades] = useState([])
  const [status, setStatus] = useState('')

  const load = () => listPaperSelections().then((rows) => {
    setSelections(rows)
    setDrafts(Object.fromEntries(rows.map((r) => [r.item_key, pickSettings(r)])))
  }).catch((e) => setError(e.message))
  useEffect(() => { load() }, [])
  useEffect(() => {
    const get = () => listPaperLiveTrades(status).then(setTrades).catch((e) => setError(e.message))
    get()
    const id = setInterval(get, 30000)
    return () => clearInterval(id)
  }, [status])

  const selected = new Set(selections.map((s) => s.item_key))
  const toggle = (key) => {
    const sel = selections.find((s) => s.item_key === key)
    const op = sel ? deletePaperSelection(sel.id) : savePaperSelection({ item_key: key, ...defaults })
    op.then(load).catch((e) => setError(e.message))
  }
  const save = (sel) => savePaperSelection({ item_key: sel.item_key, enabled: sel.enabled, ...drafts[sel.item_key] })
    .then(load).catch((e) => setError(e.message))

  return (
    <>
      <div style={S.card}>
        <h3 style={S.h3}>Patterns to paper-trade live</h3>
        <div style={S.dim}>Every hit of a selected pattern is recorded, also hits not taken (with the reason).</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }} data-testid="live-pattern-chips">
          {patterns.map((p) => (
            <span key={p.key} style={S.chip(selected.has(p.key))} onClick={() => toggle(p.key)}>
              {p.pattern_type === 'bullish' ? '▲' : '▼'} {p.name}</span>))}
        </div>
      </div>
      {selections.map((sel) => (
        <div key={sel.id} style={S.card}>
          <div style={{ ...S.row, justifyContent: 'space-between' }}>
            <h3 style={S.h3}>{patterns.find((p) => p.key === sel.item_key)?.name || sel.item_key}</h3>
            <label style={{ ...S.field, flexDirection: 'row', alignItems: 'center' }}>
              <input type="checkbox" checked={sel.enabled}
                onChange={(e) => savePaperSelection({ item_key: sel.item_key, enabled: e.target.checked })
                  .then(load).catch((er) => setError(er.message))} /> On</label>
          </div>
          {drafts[sel.item_key] && (
            <SettingsEditor value={drafts[sel.item_key]}
              onChange={(v) => setDrafts({ ...drafts, [sel.item_key]: v })} />)}
          <div><button style={S.btn} onClick={() => save(sel)}>Save settings</button></div>
        </div>))}
      <div style={S.card}>
        <div style={{ ...S.row, justifyContent: 'space-between' }}>
          <h3 style={S.h3}>Live paper trades</h3>
          <select value={status} style={S.input} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option><option value="open">Open</option>
            <option value="closed">Closed</option><option value="not_taken">Not taken</option>
          </select>
        </div>
        <TradesTable trades={trades} />
      </div>
    </>
  )
}

function newRun(i, defaults) { return { label: `Run ${i}`, items: [], ...defaults } }

function BacktestTab({ patterns, defaults, setError }) {
  const [symbols, setSymbols] = useState([])
  const [masters, setMasters] = useState([])
  const [openId, setOpenId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [form, setForm] = useState({ name: '', symbols: [], date_from: '', date_to: '', runs: [newRun(1, defaults)] })

  const loadMasters = () => listPaperBacktests().then(setMasters).catch((e) => setError(e.message))
  useEffect(() => { listSymbols().then(setSymbols).catch((e) => setError(e.message)); loadMasters() }, [])
  useEffect(() => {
    if (openId) getPaperBacktest(openId).then(setDetail).catch((e) => setError(e.message))
  }, [openId])

  const setRun = (i, v) => setForm({ ...form, runs: form.runs.map((r, j) => (j === i ? v : r)) })
  const toggleIn = (list, x) => (list.includes(x) ? list.filter((y) => y !== x) : [...list, x])
  const create = () => {
    setError(null)
    createPaperBacktest(form).then((m) => {
      setForm({ name: '', symbols: [], date_from: '', date_to: '', runs: [newRun(1, defaults)] })
      loadMasters(); setOpenId(m.id)
    }).catch((e) => setError(e.message))
  }

  return (
    <>
      <div style={S.card} data-testid="bt-form">
        <h3 style={S.h3}>New comparison</h3>
        <div style={S.row}>
          <label style={S.field}>Name<input style={{ ...S.input, width: 220 }} value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label style={S.field}>From<input type="date" style={S.input} value={form.date_from}
            onChange={(e) => setForm({ ...form, date_from: e.target.value })} /></label>
          <label style={S.field}>To<input type="date" style={S.input} value={form.date_to}
            onChange={(e) => setForm({ ...form, date_to: e.target.value })} /></label>
        </div>
        <div style={S.dim}>Stocks ({form.symbols.length} picked)</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {symbols.map((s) => (
            <span key={s.id} style={S.chip(form.symbols.includes(s.id))}
              onClick={() => setForm({ ...form, symbols: toggleIn(form.symbols, s.id) })}>{s.symbol}</span>))}
        </div>
        {form.runs.map((r, i) => (
          <div key={i} style={{ ...S.card, background: 'var(--surface-alt)' }}>
            <div style={{ ...S.row, justifyContent: 'space-between' }}>
              <label style={S.field}>Run label<input style={{ ...S.input, width: 200 }} value={r.label}
                onChange={(e) => setRun(i, { ...r, label: e.target.value })} /></label>
              {form.runs.length > 1 && <button style={S.btnGhost}
                onClick={() => setForm({ ...form, runs: form.runs.filter((_, j) => j !== i) })}>Remove run</button>}
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {patterns.map((p) => (
                <span key={p.key} style={S.chip(r.items.includes(p.key))}
                  onClick={() => setRun(i, { ...r, items: toggleIn(r.items, p.key) })}>
                  {p.pattern_type === 'bullish' ? '▲' : '▼'} {p.name}</span>))}
            </div>
            <SettingsEditor value={r} onChange={(v) => setRun(i, v)} />
          </div>))}
        <div style={S.row}>
          <button style={S.btnGhost}
            onClick={() => setForm({ ...form, runs: [...form.runs, { ...form.runs[form.runs.length - 1],
              label: `Run ${form.runs.length + 1}` }] })}>+ Add run (copy of last)</button>
          <button style={S.btn} onClick={create}>Create comparison</button>
        </div>
      </div>
      <div style={S.card}>
        <h3 style={S.h3}>Comparisons</h3>
        {!masters.length && <div style={S.dim}>None yet.</div>}
        {masters.map((m) => (
          <div key={m.id} style={{ ...S.row, cursor: 'pointer' }} onClick={() => setOpenId(m.id)}>
            <b>{m.name}</b><span style={S.dim}>{m.date_from} → {m.date_to} · {m.symbols.length} stocks</span>
            <span style={S.chip(m.status === 'done')}>{m.status}{m.status === 'running' ? ` ${fmt(m.progress_pct, 0)}%` : ''}</span>
          </div>))}
      </div>
      {detail && (
        <div style={S.card} data-testid="bt-detail">
          <h3 style={S.h3}>{detail.name} — runs compared</h3>
          {detail.status !== 'done' && <div style={S.dim}>Status: {detail.status}. Results appear here when the run finishes.</div>}
          <div style={{ overflowX: 'auto' }}>
            <table style={S.table}>
              <thead><tr>{['Run', 'Patterns', 'Stop', 'Target', 'Size', 'Trades', 'Not taken', 'Win %',
                'Avg net %', 'Net ₹'].map((h) => <th key={h} style={S.th}>{h}</th>)}</tr></thead>
              <tbody>{detail.runs.map((r) => {
                const s = r.summary || {}
                return (
                  <tr key={r.id}>
                    <td style={S.td}><b>{r.label}</b></td>
                    <td style={S.td}>{r.items.map((x) => x.item_key).join(', ')}</td>
                    <td style={S.td}>{r.stop_value} {MODE_LABEL[r.stop_mode]}</td>
                    <td style={S.td}>{r.target_value} {MODE_LABEL[r.target_mode]}</td>
                    <td style={S.td}>{r.size_mode === 'qty' ? `${r.quantity_per_trade} qty` : `₹${r.amount_per_trade}`}</td>
                    <td style={S.td}>{s.trades ?? '—'}</td><td style={S.td}>{s.not_taken ?? '—'}</td>
                    <td style={S.td}>{fmt(s.win_pct, 1)}</td>
                    <td style={{ ...S.td, color: pnlColor(s.avg_net_pct) }}>{fmt(s.avg_net_pct, 3)}</td>
                    <td style={{ ...S.td, color: pnlColor(s.net_pnl) }}>{fmt(s.net_pnl)}</td>
                  </tr>)
              })}</tbody>
            </table>
          </div>
        </div>)}
    </>
  )
}

export default function PaperTrading() {
  const [tab, setTab] = usePreference('paper.tab', 'live', (v) => v === 'live' || v === 'backtest')
  const [patterns, setPatterns] = useState([])
  const [error, setError] = useState(null)
  // the user's own defaults: new live selections and new backtest runs start from these
  const [defaults, setDefaults] = usePreference('paper.defaults', DEFAULT_SETTINGS,
    (v) => v && typeof v === 'object' && 'stop_mode' in v)
  const [draft, setDraft] = useState(null)
  const [showDefaults, setShowDefaults] = useState(false)
  useEffect(() => {
    listPatterns().then((rows) => setPatterns(rows.filter((p) => p.enabled !== false
      && (p.pattern_type === 'bullish' || p.pattern_type === 'bearish')))).catch((e) => setError(e.message))
  }, [])
  return (
    <div style={S.page}>
      <div style={{ ...S.row, justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0 }}>Paper Trading</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <button style={S.tab(tab === 'live')} onClick={() => setTab('live')}>Live</button>
          <button style={S.tab(tab === 'backtest')} onClick={() => setTab('backtest')}>Backtest</button>
        </div>
      </div>
      {error && <div style={S.error}>{error}</div>}
      <div style={S.card} data-testid="paper-defaults">
        <div style={{ ...S.row, justifyContent: 'space-between' }}>
          <div>
            <h3 style={S.h3}>Default settings</h3>
            <div style={S.dim}>New live patterns and new backtest runs start with these. Each can still be changed.</div>
          </div>
          <button style={S.btnGhost} onClick={() => { setDraft({ ...DEFAULT_SETTINGS, ...defaults }); setShowDefaults(!showDefaults) }}>
            {showDefaults ? 'Close' : 'Edit defaults'}</button>
        </div>
        {showDefaults && draft && (<>
          <SettingsEditor value={draft} onChange={setDraft} />
          <div style={S.row}>
            <button style={S.btn} onClick={() => { setDefaults(draft); setShowDefaults(false) }}>Save defaults</button>
            <button style={S.btnGhost} onClick={() => setDraft(DEFAULT_SETTINGS)}>Reset to system defaults</button>
          </div>
        </>)}
      </div>
      {tab === 'live' ? <LiveTab patterns={patterns} defaults={{ ...DEFAULT_SETTINGS, ...defaults }} setError={setError} />
        : <BacktestTab patterns={patterns} defaults={{ ...DEFAULT_SETTINGS, ...defaults }} setError={setError} />}
    </div>
  )
}
