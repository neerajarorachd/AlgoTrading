import { forwardRef, useImperativeHandle, useState } from 'react'
import CandleChart from './CandleChart.jsx'
import DepthPanel from './DepthPanel.jsx'
import Button from './kit/Button.jsx'

const DEPTH_MODE_CYCLE = { hidden: 'right', right: 'below', below: 'hidden' }

// The chart+depth "graph window" widget -- single source of truth for how
// a candle chart, its Depth panel (3-state: hidden/right/below), and
// fullscreen are assembled, used everywhere a chart appears (Market
// Watch's below-grid cards, the instrument detail view/standalone page).
// Before this existed, each caller reimplemented its own copy of the
// depth-mode layout branching and fullscreen overlay -- found live
// 2026-10-05 building a second near-identical copy for InstrumentDetail
// right after Market Watch's own below-grid cards already had one. A
// future indicator-line overlay (user-requested, not yet built) threads
// into CandleChart from here too, not duplicated per caller again.
//
// Scope boundary: this owns the chart canvas, the depth layout, and the
// fullscreen OVERLAY itself -- not the fullscreen trigger button or any
// other surrounding chrome (symbol name, close/remove, Buy/Sell, open-in-
// tab), since that legitimately differs per caller. A caller renders its
// own button and calls ref.current.requestFullscreen().
//
// depthMode/onDepthModeChange are optional -- pass both for state a caller
// needs to persist itself (Market Watch's below-grid cards, keyed per
// instrument so it survives drag-reorder); omit both to let this component
// manage its own (InstrumentDetail's case, no external persistence needed).
const GraphWindow = forwardRef(function GraphWindow(
  { instrument, depth, title, showDepth = true, depthMode: controlledDepthMode, onDepthModeChange },
  ref,
) {
  const [internalDepthMode, setInternalDepthMode] = useState('hidden')
  const [depthPanelWidth, setDepthPanelWidth] = useState(220)
  const [fullscreen, setFullscreen] = useState(false)
  const [orientation, setOrientation] = useState('horizontal')

  useImperativeHandle(ref, () => ({
    requestFullscreen: () => setFullscreen(true),
  }), [])

  const depthMode = showDepth ? (controlledDepthMode ?? internalDepthMode) : 'hidden'
  function cycleDepthMode() {
    const next = DEPTH_MODE_CYCLE[depthMode]
    if (onDepthModeChange) onDepthModeChange(next)
    else setInternalDepthMode(next)
  }

  const chart = (
    <CandleChart
      instrument={instrument}
      fillHeight={fullscreen}
      depthMode={showDepth ? depthMode : undefined}
      onCycleDepth={showDepth ? cycleDepthMode : undefined}
      onControlsWidthChange={setDepthPanelWidth}
    />
  )

  const depthPanel = showDepth && depthMode !== 'hidden' && <DepthPanel depth={depth} />

  const body = depthMode === 'below' ? (
    <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0, flex: fullscreen ? '1 1 auto' : undefined }}>
      <div style={{ flex: fullscreen ? '1 1 auto' : undefined, minHeight: 0 }}>{chart}</div>
      {depthPanel && <div style={{ marginTop: 14 }}>{depthPanel}</div>}
    </div>
  ) : (
    <div style={{ display: 'flex', minWidth: 0, minHeight: 0, flex: fullscreen ? '1 1 auto' : undefined }}>
      <div style={{ flex: '1 1 auto', minWidth: 0, minHeight: 0 }}>{chart}</div>
      {depthPanel && (
        <div style={{ flex: `0 0 ${depthPanelWidth}px`, minWidth: 0, paddingLeft: 14 }}>{depthPanel}</div>
      )}
    </div>
  )

  if (!fullscreen) return body

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'var(--bg)', zIndex: 1000,
      padding: 16, display: 'flex', flexDirection: 'column', boxSizing: 'border-box', color: 'var(--text)',
    }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <strong style={{ fontSize: 15 }}>{title ?? instrument.symbol}</strong>
        <div style={{ display: 'flex', gap: 8 }}>
          {depthMode !== 'hidden' && (
            <Button
              variant="secondary"
              onClick={() => setOrientation((o) => (o === 'horizontal' ? 'vertical' : 'horizontal'))}
            >
              {orientation === 'horizontal' ? 'Vertical layout' : 'Horizontal layout'}
            </Button>
          )}
          <Button variant="secondary" onClick={() => setFullscreen(false)}>← Back</Button>
        </div>
      </div>
      <div style={{
        flex: '1 1 auto', minHeight: 0, display: 'flex',
        flexDirection: orientation === 'horizontal' ? 'row' : 'column',
      }}
      >
        {body}
      </div>
    </div>
  )
})

export default GraphWindow
