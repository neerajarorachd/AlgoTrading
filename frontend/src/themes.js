// Single source of truth for which named themes exist in the app.
// ThemeContext.jsx validates a stored/requested theme against this list,
// and ThemeToggle.jsx renders one control per entry -- neither hardcodes
// theme names itself. theme.css still owns each theme's actual token
// VALUES (one :root[data-theme="<value>"] block per entry here); this file
// only owns which names are valid and what to call them in the UI.
//
// To add a new theme: add a :root[data-theme="X"] block to theme.css with
// its full token set, then add one entry here. Nothing else changes.
export const THEMES = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]

export const THEME_VALUES = THEMES.map((t) => t.value)
