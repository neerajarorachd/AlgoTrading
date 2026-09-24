// Order-management + SL/target formula fields living on Strategy itself
// (see backend/db/models.py's Strategy docstring) — everything
// order_backtest.py's engine_config_from_strategy reads to build a real
// EngineConfig, DB-driven instead of hand-edited per test run (see
// [[backtest_run_execution_wiring]]). All nullable: an empty field here
// means "use the engine default" server-side, so a strategy can set only
// what it cares about.

const SL_FORMULA_TYPES = [
  { value: '', label: '(default: ATR/neckline)' },
  { value: 'fixed_percent', label: 'Fixed % off entry' },
  { value: 'fixed_points', label: 'Fixed points off entry' },
  { value: 'atr', label: 'ATR multiple' },
]
const TARGET_FORMULA_TYPES = [
  { value: '', label: '(default: ATR/neckline)' },
  { value: 'fixed_percent', label: 'Fixed % off entry' },
  { value: 'fixed_points', label: 'Fixed points off entry' },
  { value: 'risk_reward', label: 'Risk:reward ratio' },
]

function numField(values, onChange, key, label, opts = {}) {
  return (
    <label>
      {label}<br />
      <input
        type="number" step={opts.step ?? 'any'} style={{ width: opts.width ?? 110 }}
        value={values[key] ?? ''}
        onChange={(e) => onChange({ ...values, [key]: e.target.value === '' ? null : Number(e.target.value) })}
      />
    </label>
  )
}

function textField(values, onChange, key, label, opts = {}) {
  return (
    <label>
      {label}<br />
      <input
        type="text" style={{ width: opts.width ?? 180 }} placeholder={opts.placeholder}
        value={values[key] ?? ''}
        onChange={(e) => onChange({ ...values, [key]: e.target.value === '' ? null : e.target.value })}
      />
    </label>
  )
}

function checkField(values, onChange, key, label) {
  return (
    <label>
      <input
        type="checkbox" checked={!!values[key]}
        onChange={(e) => onChange({ ...values, [key]: e.target.checked })}
      />
      {' '}{label}
    </label>
  )
}

function timeField(values, onChange, key, label) {
  return (
    <label>
      {label}<br />
      <input
        type="time" style={{ width: 110 }}
        value={values[key] ?? ''}
        onChange={(e) => onChange({ ...values, [key]: e.target.value === '' ? null : e.target.value })}
      />
    </label>
  )
}

const fieldsetStyle = { border: '1px solid #ddd', borderRadius: 6, padding: 12, marginBottom: 12 }
const rowStyle = { display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }

export default function StrategyOrderManagementForm({ values, onChange }) {
  const slMode = values.sl_formula_type
  const targetMode = values.target_formula_type

  return (
    <div>
      <fieldset style={fieldsetStyle}>
        <legend>Stop loss / target</legend>
        <div style={rowStyle}>
          <label>
            Direction<br />
            <select
              value={values.direction ?? ''}
              onChange={(e) => onChange({ ...values, direction: e.target.value || null })}
            >
              <option value="">(both)</option>
              <option value="bull">Bull only</option>
              <option value="bear">Bear only</option>
            </select>
          </label>
          <label>
            Stop-loss formula<br />
            <select
              value={slMode ?? ''}
              onChange={(e) => onChange({ ...values, sl_formula_type: e.target.value || null })}
            >
              {SL_FORMULA_TYPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          {(slMode === 'fixed_percent' || slMode === 'fixed_points') &&
            numField(values, onChange, 'sl_fixed_value', slMode === 'fixed_percent' ? 'SL (e.g. 0.004 = 0.4%)' : 'SL (points)', { step: '0.0001' })}
          {slMode === 'atr' && numField(values, onChange, 'sl_atr_multiplier', 'ATR multiplier')}

          <label>
            Target formula<br />
            <select
              value={targetMode ?? ''}
              onChange={(e) => onChange({ ...values, target_formula_type: e.target.value || null })}
            >
              {TARGET_FORMULA_TYPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          {(targetMode === 'fixed_percent' || targetMode === 'fixed_points') &&
            numField(values, onChange, 'target_fixed_value', targetMode === 'fixed_percent' ? 'Target (e.g. 0.008 = 0.8%)' : 'Target (points)', { step: '0.0001' })}
          {targetMode === 'risk_reward' &&
            numField(values, onChange, 'target_risk_reward_ratio', 'Risk:reward ratio')}
        </div>
      </fieldset>

      <fieldset style={fieldsetStyle}>
        <legend>Position sizing &amp; risk</legend>
        <div style={rowStyle}>
          {numField(values, onChange, 'capital_per_trade', 'Capital pool (₹)', { step: '1000' })}
          {numField(values, onChange, 'max_vol_per_call', 'Max qty per order')}
          {numField(values, onChange, 'max_orders_at_a_time', 'Max concurrent orders')}
          {numField(values, onChange, 'daily_max_trade_count', 'Max trades/day')}
          <label>
            <input
              type="checkbox" checked={!!values.exit_at_loss}
              onChange={(e) => onChange({ ...values, exit_at_loss: e.target.checked })}
            />
            {' '}Halt new entries after N losses
          </label>
          {numField(values, onChange, 'exit_at_loss_count', 'N (loss count)')}
          {numField(values, onChange, 'round_trip_cost_rate', 'Round-trip cost rate', { step: '0.0001' })}
        </div>
      </fieldset>

      <fieldset style={fieldsetStyle}>
        <legend>Order-sequence margin multipliers</legend>
        <div style={rowStyle}>
          {numField(values, onChange, 'first_order_quantity', '1st order fixed qty (overrides sizing)')}
          {numField(values, onChange, 'order1_margin_multiplier', '1st order multiplier', { step: '0.5' })}
          {numField(values, onChange, 'order2_margin_multiplier', '2nd order multiplier', { step: '0.5' })}
          {numField(values, onChange, 'order3_margin_multiplier', '3rd+ order multiplier', { step: '0.5' })}
        </div>
      </fieldset>

      <fieldset style={fieldsetStyle}>
        <legend>Trading window (IST)</legend>
        <div style={rowStyle}>
          {timeField(values, onChange, 'trading_start_time', 'New orders from')}
          {timeField(values, onChange, 'new_order_end_time', 'New orders until')}
          {timeField(values, onChange, 'trading_end_time', 'Square-off at')}
        </div>
      </fieldset>

      <fieldset style={fieldsetStyle}>
        <legend>Risk management (from the HINDCOPPER double_top investigation)</legend>
        <div style={rowStyle}>
          {numField(values, onChange, 'max_daily_loss_pct', 'Max daily GROSS loss (e.g. 0.01 = 1%, proven to help)', { step: '0.001', width: 160 })}
          {checkField(values, onChange, 'compounding', 'Compounding (size off running fund pool)')}
          {checkField(values, onChange, 'itemized_costs', 'Real itemized costs (brokerage/STT/exchange/SEBI/stamp/GST)')}
        </div>
      </fieldset>

      <fieldset style={fieldsetStyle}>
        <legend>Multi-candle fills &amp; liquidity</legend>
        <div style={rowStyle}>
          {checkField(values, onChange, 'spread_fills', 'Spread fills across multiple candles')}
          {numField(values, onChange, 'max_fill_candles', 'Max candles to fill entry', { width: 90 })}
          {numField(values, onChange, 'max_exit_candles', 'Max candles to fill exit', { width: 90 })}
          {numField(values, onChange, 'max_fill_price_drift_pct', 'Abandon fill if price drifts (fraction)', { step: '0.0005', width: 130 })}
          {numField(values, onChange, 'liquidity_safety_divisor', 'Liquidity divisor (qty capped at volume / N)', { width: 130 })}
        </div>
      </fieldset>

      <fieldset style={fieldsetStyle}>
        <legend>Volume entry gate</legend>
        <div style={rowStyle}>
          {numField(values, onChange, 'min_avg_volume_multiple', 'Refuse entry unless mean volume ≥ N × order qty', { step: '0.1', width: 220 })}
          {numField(values, onChange, 'min_avg_volume_lookback', 'Candles to average (default 5)', { width: 90 })}
        </div>
      </fieldset>

      <fieldset style={fieldsetStyle}>
        <legend>Experimental (tested, not recommended as defaults)</legend>
        <div style={rowStyle}>
          {checkField(values, onChange, 'exit_on_macd_reversal', 'Exit early on MACD reversal — made results worse on 1min')}
          {textField(values, onChange, 'win_streak_multipliers', 'Win-streak size multipliers — did not reliably help', { placeholder: '0.5,1.0,2.0,3.0,4.0', width: 200 })}
        </div>
      </fieldset>
    </div>
  )
}

// Nullable on the backend (null = "use the engine default"), but rendered
// as a plain checkbox here — same collapse-to-boolean convention
// exit_at_loss already used before these were added.
const BOOLEAN_FIELDS = ['exit_at_loss', 'compounding', 'itemized_costs', 'spread_fills', 'exit_on_macd_reversal']

export const EMPTY_ORDER_MANAGEMENT_FORM = {
  direction: null, sl_formula_type: null, sl_atr_multiplier: null, sl_fixed_value: null,
  target_formula_type: null, target_risk_reward_ratio: null, target_fixed_value: null,
  capital_per_trade: null, max_vol_per_call: null, max_orders_at_a_time: null,
  daily_max_trade_count: null, exit_at_loss: false, exit_at_loss_count: null,
  round_trip_cost_rate: null, first_order_quantity: null,
  order1_margin_multiplier: null, order2_margin_multiplier: null, order3_margin_multiplier: null,
  trading_start_time: null, new_order_end_time: null, trading_end_time: null,
  max_daily_loss_pct: null, exit_on_macd_reversal: false, win_streak_multipliers: null,
  spread_fills: false, max_fill_candles: null, max_exit_candles: null, max_fill_price_drift_pct: null,
  liquidity_safety_divisor: null, itemized_costs: false, compounding: false,
  min_avg_volume_multiple: null, min_avg_volume_lookback: null,
}

export function orderManagementValuesFromStrategy(s) {
  const out = {}
  for (const key of Object.keys(EMPTY_ORDER_MANAGEMENT_FORM)) {
    out[key] = BOOLEAN_FIELDS.includes(key) ? !!s[key] : (s[key] ?? null)
  }
  return out
}
