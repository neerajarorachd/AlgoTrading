// Event diagrams for the live chart (ALGO-25): turns a candle's events into
// shapes in data space (bar times + prices), and shapes into SVG once the
// chart supplies its coordinate functions. Pure -- no chart or React here, so
// any chart widget can reuse it.
//
//   candle formation / price action -> ellipse around the 1-3 candles it spans
//   structure (swing, BOS, CHoCH)     -> rectangle, labelled HH/LH/HL/LL or BOS/CHoCH
//   graph formation                   -> polyline through its swing points + neckline
//   indicator crossover                -> nothing to draw (listed in the tooltip)

const CANDLES_SPANNED = {
  morning_star: 3, evening_star: 3, three_white_soldiers: 3, three_black_crows: 3,
  bullish_engulfing: 2, bearish_engulfing: 2, piercing_line: 2, dark_cloud_cover: 2,
  tweezer_top: 2, tweezer_bottom: 2,
  vwap_rejection_bear: 2, vwap_rejection_bull: 2, vwap_rejection_bear_strong: 2, vwap_rejection_bull_strong: 2,
}

// swing points a graph formation is read from (activity_engine.py's detectors)
const FORMATION_POINTS = {
  double_top: 3, double_bottom: 3, triple_top: 5, triple_bottom: 5,
  double_top_ed: 2, double_bottom_ed: 2,
  ascending_triangle: 4, descending_triangle: 4, symmetrical_triangle: 4,
  rising_wedge: 4, falling_wedge: 4, rectangle: 4,
}

const STRUCTURE_LABEL = {
  bullish_break_of_structure: 'BOS ↑', bearish_break_of_structure: 'BOS ↓',
  bullish_structure_shift: 'CHoCH ↑', bearish_structure_shift: 'CHoCH ↓',
}

export function toUnix(ts) {
  return Math.floor(new Date(ts).getTime() / 1000)
}

export function humanize(activity) {
  return activity.replace(/_/g, ' ')
}

/** Everything the shape builder needs, prepared once per data change. */
export function indexData(candles, events) {
  const byTime = new Map()
  candles.forEach((c, i) => byTime.set(toUnix(c.ts), i))
  const swings = events
    .filter((e) => e.activity === 'swing_high' || e.activity === 'swing_low')
    .map((e) => {
      const t = toUnix(e.ts)
      const i = byTime.get(t)
      const c = i != null ? candles[i] : null
      return c ? { t, kind: e.activity === 'swing_high' ? 'high' : 'low', price: e.activity === 'swing_high' ? c.high : c.low } : null
    })
    .filter(Boolean)
    .sort((a, b) => a.t - b.t)
  return { candles, byTime, swings }
}

function span(data, i, n) {
  const from = Math.max(0, i - n + 1)
  const bars = data.candles.slice(from, i + 1)
  return {
    t0: toUnix(data.candles[from].ts), t1: toUnix(data.candles[i].ts),
    high: Math.max(...bars.map((b) => b.high)), low: Math.min(...bars.map((b) => b.low)),
  }
}

function swingsBefore(data, t, kind) {
  return data.swings.filter((s) => s.t < t && (!kind || s.kind === kind))
}

/** Shapes for one event; [] when it has nothing to draw. */
export function shapesForEvent(ev, data) {
  const t = toUnix(ev.ts)
  const i = data.byTime.get(t)
  if (i == null) return []
  const label = humanize(ev.activity)
  const dir = ev.direction

  if (ev.activity_type === 'candle_pattern' || ev.activity_type === 'price_action') {
    const s = span(data, i, CANDLES_SPANNED[ev.activity] ?? 1)
    return [{ kind: 'ellipse', ...s, dir, label }]
  }

  if (ev.activity_type === 'structure') {
    if (ev.activity === 'swing_high' || ev.activity === 'swing_low') {
      const high = ev.activity === 'swing_high'
      const c = data.candles[i]
      const prev = swingsBefore(data, t, high ? 'high' : 'low').at(-1)
      const level = high ? c.high : c.low
      const tag = prev == null ? (high ? 'SH' : 'SL')
        : high ? (level > prev.price ? 'HH' : 'LH') : (level > prev.price ? 'HL' : 'LL')
      const s = span(data, Math.min(i + 1, data.candles.length - 1), Math.min(3, i + 2))
      return [{ kind: 'rect', ...s, dir: high ? 'bear' : 'bull', label: tag }]
    }
    // BOS / CHoCH: from the swing level that was broken to the breaking candle
    const bull = ev.activity.startsWith('bullish')
    const broken = swingsBefore(data, t, bull ? 'high' : 'low').at(-1)
    const c = data.candles[i]
    if (!broken) return [{ kind: 'rect', ...span(data, i, 1), dir, label: STRUCTURE_LABEL[ev.activity] ?? label }]
    return [{
      kind: 'rect', t0: broken.t, t1: t,
      high: Math.max(broken.price, c.high), low: Math.min(broken.price, c.low),
      dir, label: STRUCTURE_LABEL[ev.activity] ?? label,
    }]
  }

  if (ev.activity_type === 'graph_formation') {
    const n = FORMATION_POINTS[ev.activity] ?? 4
    const pts = swingsBefore(data, t + 1).slice(-n).map((s) => ({ t: s.t, p: s.price }))
    if (pts.length < 2) return [{ kind: 'ellipse', ...span(data, i, 1), dir, label }]
    pts.push({ t, p: data.candles[i].close })
    return [{ kind: 'polyline', points: pts, neckline: ev.neckline ?? null, t0: pts[0].t, t1: t, dir, label }]
  }

  return []
}

export function shapesForEvents(events, data) {
  return events.flatMap((ev) => shapesForEvent(ev, data))
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]))
}

/**
 * SVG markup for `shapes`. `x(t)` / `y(p)` map bar time and price to pixels
 * (null when off the chart); `colors` = {bull, bear, neutral}. `dense`
 * (show-all mode): no text labels and lighter strokes -- hundreds of labelled
 * shapes on screen at once are unreadable; the hover list names them.
 */
export function shapesToSvg(shapes, x, y, width, height, colors, barWidth = 6, dense = false) {
  const out = []
  const sw = dense ? 0.55 : 1   // stroke width factor
  const op = dense ? 0.6 : 1    // stroke opacity
  const col = (dir) => (dir === 'bull' ? colors.bull : dir === 'bear' ? colors.bear : colors.neutral)
  const pad = Math.max(barWidth * 0.8, 4)
  for (const s of shapes) {
    const color = col(s.dir)
    if (s.kind === 'ellipse' || s.kind === 'rect') {
      const x0 = x(s.t0); const x1 = x(s.t1); const yTop = y(s.high); const yBot = y(s.low)
      if (x0 == null || x1 == null || yTop == null || yBot == null) continue
      const left = Math.min(x0, x1) - pad; const right = Math.max(x0, x1) + pad
      const top = yTop - 6; const bottom = yBot + 6
      if (s.kind === 'ellipse') {
        out.push(`<ellipse cx="${(left + right) / 2}" cy="${(top + bottom) / 2}" rx="${Math.max((right - left) / 2, 8)}" ry="${Math.max((bottom - top) / 2, 8)}" fill="none" stroke="${color}" stroke-width="${1.6 * sw}" stroke-opacity="${op}"/>`)
      } else {
        out.push(`<rect x="${left}" y="${top}" width="${right - left}" height="${Math.max(bottom - top, 6)}" rx="2" fill="${color}" fill-opacity="${dense ? 0.04 : 0.08}" stroke="${color}" stroke-width="${1.4 * sw}" stroke-opacity="${op}"/>`)
      }
      if (!dense) out.push(`<text x="${left}" y="${Math.max(top - 4, 10)}" font-size="10.5" font-weight="600" fill="${color}">${esc(s.label)}</text>`)
    } else if (s.kind === 'polyline') {
      const pts = s.points.map((pt) => [x(pt.t), y(pt.p)])
      if (pts.some(([px, py]) => px == null || py == null)) continue
      out.push(`<polyline points="${pts.map(([px, py]) => `${px},${py}`).join(' ')}" fill="none" stroke="${color}" stroke-width="${1.6 * sw}" stroke-opacity="${op}" stroke-linejoin="round"/>`)
      pts.forEach(([px, py]) => out.push(`<circle cx="${px}" cy="${py}" r="2.6" fill="${color}"/>`))
      if (s.neckline != null && y(s.neckline) != null) {
        const ny = y(s.neckline)
        out.push(`<line x1="${pts[0][0]}" x2="${pts[pts.length - 1][0]}" y1="${ny}" y2="${ny}" stroke="${color}" stroke-width="1.2" stroke-dasharray="4 3"/>`)
      }
      const top = Math.min(...pts.map(([, py]) => py))
      if (!dense) out.push(`<text x="${pts[0][0]}" y="${Math.max(top - 6, 10)}" font-size="10.5" font-weight="600" fill="${color}">${esc(s.label)}</text>`)
    }
  }
  return `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg" style="position:absolute;inset:0;overflow:visible">${out.join('')}</svg>`
}
