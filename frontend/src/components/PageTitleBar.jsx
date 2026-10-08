// Full-width page title bar (2026-10-08, first on Workspace): the page name
// on the theme's title colours (--title-bg / --title-text in theme.css),
// with an optional slot on the right for page-level controls. Standalone --
// any page can put it first in its layout.
export default function PageTitleBar({ title, right = null }) {
  return (
    <div
      data-page-title-bar
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
        background: 'var(--title-bg)', color: 'var(--title-text)',
        paddingBlock: 9, paddingInline: 'var(--space-5)',
      }}
    >
      <h1 style={{
        margin: 0, fontSize: 20, fontWeight: 700, letterSpacing: '.06em', textTransform: 'uppercase',
        color: 'inherit', lineHeight: 1.3,
      }}
      >
        {title}
      </h1>
      {right}
    </div>
  )
}
