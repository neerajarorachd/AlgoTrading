// Hit review (2026-10-16): why each of today's hits of one pattern or raw
// signal fired -- for a pattern, every condition with what it requires and
// the actual value on that candle (✗ = it did not hold, the pattern still
// formed at 80%); for a raw signal, every strength measure against its
// minimum. Opened as a Workspace tab from the counts in the chart's
// Signals / Patterns tabs. Standalone: give it kind, key, symbol.
import { useCallback, useEffect, useState } from 'react'
import { getReview } from '../api/client.js'

const TFS = ['1min', '3min', '5min']
const fmt = (v) => (v === null || v === undefined ? 'unknown' : typeof v === 'number'
  ? (Math.abs(v) >= 1000 ? v.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : String(+v.toFixed(4)))
  : v === true ? 'yes' : v === false ? 'no' : String(v))
const TYPE_COLOR = { bullish: 'var(--good)', bearish: 'var(--critical)', caution: '#d9739b', avoid: 'var(--critical)' }

export default function HitReview({ kind, code, symbol, exchangeSegment = 'NSE_EQ' }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [tf, setTf] = useState('')
  const [allStocks, setAllStocks] = useState(false)
  const [onlyFailing, setOnlyFailing] = useState(false)

  const load = useCallback(() => {
    getReview({ kind, key: code, symbol, exchange_segment: exchangeSegment, all: allStocks ? 1 : undefined })
      .then((r) => { setData(r); setError(null) })
      .catch((e) => setError(e.message))
  }, [kind, code, symbol, exchangeSegment, allStocks])
  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t) }, [load])

  if (error) return <p style={{ color: 'var(--critical)' }}>{error}</p>
  if (!data) return <p style={{ color: 'var(--text-faint)' }}>Loading the review…</p>

  const isPattern = data.kind === 'pattern'
  const failed = (h) => (isPattern ? h.met < h.of : h.strong === false)
  const hits = data.hits.filter((h) => (!tf || h.timeframe === tf) && (!onlyFailing || failed(h)))
  const byTf = Object.fromEntries(TFS.map((t) => [t, data.hits.filter((h) => h.timeframe === t).length]))
  const failing = data.hits.filter(failed).length
  const columns = isPattern ? data.conditions.map((c) => c.formula) : data.measures.map((m) => m.measure)

  return (
    <div data-hit-review={`${data.kind}:${data.key}`} style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '6px 14px' }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>{data.name}</h2>
        {data.type && <span style={{ fontSize: 11.5, fontWeight: 700, color: TYPE_COLOR[data.type], border: '1px solid currentColor', borderRadius: 99, padding: '0 8px' }}>{data.type}</span>}
        <span style={{ color: 'var(--text-dim)', fontSize: 13 }}>
          {allStocks ? 'all stocks' : symbol} · today · <b data-review-total>{data.hits.length}</b> {isPattern ? 'hits' : 'occurrences'} (1m {byTf['1min']} · 3m {byTf['3min']} · 5m {byTf['5min']})
          {' · '}{failing} {isPattern ? 'formed with a failing condition' : 'below a strength minimum'}
          {isPattern && data.weight != null && <> · weight {data.weight}</>}
        </span>
        <button type="button" onClick={load} style={{ marginLeft: 'auto', fontSize: 12, padding: '3px 10px', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', background: 'transparent', color: 'var(--text-dim)', cursor: 'pointer' }}>Refresh</button>
      </div>

      {isPattern && (
        <p style={{ margin: 0, fontSize: 12.5, color: 'var(--text-dim)', maxWidth: '80ch' }}>
          A pattern forms when at least {Math.round((data.formed_at ?? 0.8) * 100)}% of its conditions hold on the candle.
          A red ✗ below is a condition that did not hold, but the pattern still formed.
        </p>
      )}

      <div data-review-definition style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
          <thead><tr style={TH_ROW}>{isPattern
            ? <><th style={TH}>#</th><th style={TH}>Condition</th><th style={{ ...TH, textAlign: 'right' }}>Failed on</th></>
            : <><th style={TH}>Strength measure</th><th style={TH}>Formula</th>{TFS.map((t) => <th key={t} style={{ ...TH, textAlign: 'right' }}>Minimum {t.replace('min', 'm')}</th>)}</>}
          </tr></thead>
          <tbody>
            {isPattern
              ? data.conditions.map((c, i) => (
                  <tr key={i}><td style={TD}>{i + 1}</td><td style={{ ...TD, ...MONO }}>{c.formula}</td>
                    <td style={{ ...TD, textAlign: 'right' }}>{data.hits.filter((h) => h.conditions[i] && !h.conditions[i].met).length} of {data.hits.length}</td></tr>))
              : data.measures.length
                ? data.measures.map((m) => (
                    <tr key={m.measure}><td style={TD}>{m.measure}</td><td style={{ ...TD, ...MONO }}>{m.formula}</td>
                      {TFS.map((t) => <td key={t} style={{ ...TD, textAlign: 'right' }}>{m.minimum[t] == null ? '—' : fmt(m.minimum[t])}</td>)}</tr>))
                : <tr><td style={TD} colSpan={5}>This signal has no strength measures: every occurrence counts.</td></tr>}
          </tbody>
        </table>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px 16px', fontSize: 12.5 }}>
        <div role="radiogroup" aria-label="Timeframe" style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
          {['', ...TFS].map((t) => (
            <button key={t || 'all'} type="button" role="radio" aria-checked={tf === t} data-review-tf={t || 'all'} onClick={() => setTf(t)}
              style={{ border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600, padding: '4px 10px', color: tf === t ? 'var(--text-on-accent)' : 'var(--text-dim)', background: tf === t ? 'var(--accent)' : 'transparent' }}
            >{t ? t.replace('min', 'm') : 'All'}</button>
          ))}
        </div>
        <label style={LABEL}><input type="checkbox" data-review-all-stocks checked={allStocks} onChange={(e) => setAllStocks(e.target.checked)} /> All stocks</label>
        <label style={LABEL}><input type="checkbox" data-review-only-failing checked={onlyFailing} onChange={(e) => setOnlyFailing(e.target.checked)} /> {isPattern ? 'Only hits with a failing condition' : 'Only below a minimum'}</label>
        <span style={{ color: 'var(--text-faint)' }}>showing {hits.length}</span>
      </div>

      <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', overflowX: 'auto' }}>
        {hits.length === 0 ? <p style={{ padding: 12, margin: 0, color: 'var(--text-faint)' }}>No {isPattern ? 'hits' : 'occurrences'} match.</p> : (
          <table data-review-hits style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
            <thead><tr style={TH_ROW}>
              {allStocks && <th style={TH}>Stock</th>}<th style={TH}>TF</th><th style={TH}>Time</th>
              {isPattern ? <><th style={{ ...TH, textAlign: 'right' }}>Met</th><th style={{ ...TH, textAlign: 'right' }}>Score</th></> : <th style={TH}>Strong enough</th>}
              {columns.map((c, i) => <th key={i} style={{ ...TH, ...MONO, textTransform: 'none', letterSpacing: 0 }}>{isPattern ? `${i + 1}. ` : ''}{c}</th>)}
            </tr></thead>
            <tbody>
              {hits.map((h, r) => (
                <tr key={r} data-review-hit>
                  {allStocks && <td style={TD}>{h.symbol}</td>}<td style={TD}>{h.timeframe.replace('min', 'm')}</td><td style={TD}>{h.time}</td>
                  {isPattern ? <><td style={{ ...TD, textAlign: 'right' }}>{h.met}/{h.of}</td><td style={{ ...TD, textAlign: 'right' }}>{h.score}</td></>
                    : <td style={{ ...TD, color: h.strong === false ? 'var(--critical)' : h.strong ? 'var(--good)' : 'var(--text-faint)' }}>{h.strong == null ? 'no minimums' : h.strong ? 'yes' : 'no'}</td>}
                  {(isPattern ? h.conditions : h.measures).map((c, i) => (
                    <td key={i} data-met={String(c.met)} style={{ ...TD, whiteSpace: 'nowrap', color: c.met === false ? 'var(--critical)' : c.met ? 'var(--good)' : 'var(--text-dim)', background: c.met === false ? 'var(--critical-soft)' : undefined }}>
                      {c.met === false ? '✗' : c.met ? '✓' : '·'} {fmt(c.actual)}
                      {c.required && <div style={{ fontSize: 11, color: 'var(--text-faint)', whiteSpace: 'normal' }}>{c.required}</div>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

const TH_ROW = { background: 'var(--surface-alt, transparent)' }
const TH = { textAlign: 'left', padding: '6px 10px', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.04em', color: 'var(--text-faint)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const TD = { padding: '6px 10px', borderBottom: '1px solid var(--border)', verticalAlign: 'top' }
const MONO = { fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace', fontSize: 12 }
const LABEL = { display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--text-dim)', cursor: 'pointer' }
