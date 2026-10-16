// Candlesticks whose body is a set share of its slot (2026-10-16). The
// library's own candlestick series fixes the body at ~80% of the bar spacing;
// the user wants Dhan-like candles -- the body about half its old width while
// the slot (body + gap) keeps 65% of its old size -- so the body is ~60% of
// the slot. Drawn as a lightweight-charts 4.x custom series; everything else
// (markers, price lines, autoscale, crosshair) uses the same series API.
//
// Data items: { time, open, high, low, close } (whitespace: { time } only).
// Options: upColor, downColor, bodyRatio (body width / bar spacing).

export const DEFAULT_BODY_RATIO = 0.6

class ThinCandleRenderer {
  constructor() {
    this._data = null
    this._options = null
  }

  update(data, options) {
    this._data = data
    this._options = options
  }

  draw(target, priceToCoordinate) {
    const data = this._data
    const opts = this._options
    if (!data || !opts || !data.visibleRange || !data.bars.length) return
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
      }
    })
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
