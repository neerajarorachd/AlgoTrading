// Candlesticks whose body is a set share of its slot (2026-10-16). The
// library's own candlestick series fixes the body at ~80% of the bar spacing;
// the user wants Dhan-like candles -- the body about half its old width while
// the slot (body + gap) keeps 65% of its old size -- so the body is ~60% of
// the slot. Drawn as a lightweight-charts 4.x custom series; everything else
// (markers, price lines, autoscale, crosshair) uses the same series API.
//
// Data items: { time, t, open, high, low, close } -- t = time again (the
// library strips `time` before draw); whitespace: { time } only.
// Options: upColor, downColor, bodyRatio (body width / bar spacing).
//
// It also draws the chart's event marks (2026-10-16): one mark per kind on a
// candle -- circle = raw signal, square = pattern, triangle = strategy --
// exactly as wide as the candle's body (MARK_MIN..MARK_MAX px), stacked above
// the bar (a buy triangle below it). Set with series.setMarks(Map(time ->
// [{ shape, color }])); the library's own markers had a 12 px floor, so they
// dwarfed the candles when zoomed out.

export const DEFAULT_BODY_RATIO = 0.6
export const MARK_MIN = 3
export const MARK_MAX = 12
// space between the wick tip and the first mark, and between stacked marks:
// half a mark's width, at least 3 px (user, 2026-10-27: "a little distance,
// like candle high wick + some ratio")
export function markGap(w) {
  return Math.max(3, w * 0.5)
}

/** Where each of a candle's marks sits (CSS px boxes), the same layout the
 * renderer paints -- for click hit-testing. highY/lowY: the bar's high/low. */
export function markBoxes(marks, cx, highY, lowY, w) {
  const gap = markGap(w)
  let above = Math.min(highY, lowY) - gap
  let below = Math.max(highY, lowY) + gap
  return marks.map((m) => {
    const up = m.shape === 'triangleUp'
    const y0 = up ? below : above - w
    if (up) below += w + gap
    else above -= w + gap
    return { ...m, x0: cx - w / 2, x1: cx + w / 2, y0, y1: y0 + w }
  })
}

/** A mark's width (CSS px) for a bar spacing: the body width, kept in [MARK_MIN, MARK_MAX]. */
export function markWidth(barSpacing, bodyRatio = DEFAULT_BODY_RATIO) {
  return Math.min(MARK_MAX, Math.max(MARK_MIN, barSpacing * bodyRatio))
}

class ThinCandleRenderer {
  constructor() {
    this._data = null
    this._options = null
    this._marks = new Map()
    this.marksDrawn = 0 // marks painted in the last frame (read by the UI suite)
  }

  update(data, options) {
    this._data = data
    this._options = options
  }

  draw(target, priceToCoordinate) {
    const data = this._data
    const opts = this._options
    if (!data || !opts || !data.visibleRange || !data.bars.length) return
    this.marksDrawn = 0
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      const wickWidth = Math.max(1, Math.floor(hr))
      // keep the body odd-width when possible so the wick sits in its middle
      let bodyWidth = Math.max(wickWidth, Math.round(data.barSpacing * opts.bodyRatio * hr))
      if ((bodyWidth - wickWidth) % 2) bodyWidth = Math.max(wickWidth, bodyWidth - 1)
      for (let i = data.visibleRange.from; i < data.visibleRange.to; i++) {
        const bar = data.bars[i]
        const d = bar?.originalData
        if (!d || d.close == null || d.open == null) continue
        const o = priceToCoordinate(d.open)
        const c = priceToCoordinate(d.close)
        const h = priceToCoordinate(d.high)
        const l = priceToCoordinate(d.low)
        if (o == null || c == null || h == null || l == null) continue
        ctx.fillStyle = d.close >= d.open ? opts.upColor : opts.downColor
        const x = Math.round(bar.x * hr)
        const wickLeft = x - Math.floor(wickWidth / 2)
        const top = Math.round(Math.min(h, l) * vr)
        ctx.fillRect(wickLeft, top, wickWidth, Math.max(1, Math.round(Math.max(h, l) * vr) - top))
        const bodyTop = Math.round(Math.min(o, c) * vr)
        const bodyBottom = Math.round(Math.max(o, c) * vr)
        ctx.fillRect(wickLeft - (bodyWidth - wickWidth) / 2, bodyTop, bodyWidth, Math.max(1, bodyBottom - bodyTop))
        // the library strips `time` from the item it hands back, so items carry
        // their own copy in `t` (toBar) for the marks lookup
        const marks = this._marks.get(d.t)
        if (marks?.length) this._drawMarks(ctx, marks, bar.x * hr, Math.min(h, l) * vr, Math.max(h, l) * vr, data.barSpacing, opts.bodyRatio, hr, vr)
      }
    })
  }

  _drawMarks(ctx, marks, cx, top, bottom, barSpacing, bodyRatio, hr, vr) {
    const wCss = markWidth(barSpacing, bodyRatio)
    const w = wCss * hr
    const gap = markGap(wCss) * vr
    let above = top - gap // next free spot above the high
    let below = bottom + gap // and below the low
    for (const m of marks) {
      ctx.fillStyle = m.color
      const up = m.shape === 'triangleUp'
      const y0 = up ? below : above - w // top of this mark's box
      if (up) below += w + gap
      else above -= w + gap
      ctx.beginPath()
      if (m.shape === 'circle') {
        ctx.arc(cx, y0 + w / 2, w / 2, 0, 2 * Math.PI)
      } else if (m.shape === 'square') {
        ctx.rect(cx - w / 2, y0, w, w)
      } else if (up) {
        ctx.moveTo(cx, y0); ctx.lineTo(cx + w / 2, y0 + w); ctx.lineTo(cx - w / 2, y0 + w)
      } else { // triangleDown
        ctx.moveTo(cx - w / 2, y0); ctx.lineTo(cx + w / 2, y0); ctx.lineTo(cx, y0 + w)
      }
      ctx.closePath()
      ctx.fill()
      this.marksDrawn += 1
    }
  }
}

export class ThinCandleSeries {
  constructor() {
    this._renderer = new ThinCandleRenderer()
  }

  renderer() {
    return this._renderer
  }

  update(data, options) {
    this._renderer.update(data, options)
  }

  /** marks: Map(unix time -> [{ shape: circle|square|triangleUp|triangleDown, color }]) */
  setMarks(marks) {
    this._renderer._marks = marks
  }

  // high, low, current (close): autoscale, crosshair, last-price label, markers
  priceValueBuilder(row) {
    return [row.high, row.low, row.close]
  }

  isWhitespace(row) {
    return row.close === undefined
  }

  defaultOptions() {
    return {
      upColor: '#26a69a',
      downColor: '#ef5350',
      bodyRatio: DEFAULT_BODY_RATIO,
      priceLineVisible: true,
      lastValueVisible: true,
    }
  }
}
