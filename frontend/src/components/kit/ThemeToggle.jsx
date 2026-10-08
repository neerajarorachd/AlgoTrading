import { useTheme } from '../../ThemeContext.jsx'
import { THEMES } from '../../themes.js'

// 'Auto' + whatever's in themes.js -- 'system' explicitly means "no stamp,
// follow the OS," not a synonym for the first named theme. See
// ThemeContext.jsx. The segmented-control style reads fine up to a handful
// of themes; if themes.js grows past that, this becomes a dropdown instead
// -- not a reason to hardcode the option list here meanwhile.
const OPTIONS = [{ value: 'system', label: 'Auto' }, ...THEMES]
export default function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  return (
    <div
      role="radiogroup" aria-label="Theme"
      style={{
        display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
        overflow: 'hidden',
      }}
    >
      {OPTIONS.map((opt) => {
        const active = theme === opt.value
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => setTheme(opt.value)}
            style={{
              border: 'none', cursor: 'pointer', fontFamily: 'inherit', fontSize: 12, fontWeight: 600,
              padding: '5px 10px', color: active ? 'var(--text-on-accent)' : 'var(--text-dim)',
              background: active ? 'var(--accent)' : 'transparent',
            }}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}
