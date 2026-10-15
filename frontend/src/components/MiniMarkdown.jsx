// A small Markdown renderer for the app's own reference docs (the ready
// reckoner): headings, paragraphs, bullet/numbered lists, tables, code
// blocks, quotes, rules, and inline code/bold/italic. Builds React elements
// (never innerHTML), so doc text can't inject markup. Not a general parser.

function inline(text, keyBase = '') {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g)
  return parts.filter(Boolean).map((p, i) => {
    const k = `${keyBase}${i}`
    if (p.startsWith('`') && p.endsWith('`')) return <code key={k} style={CODE}>{p.slice(1, -1)}</code>
    if (p.startsWith('**') && p.endsWith('**')) return <strong key={k}>{p.slice(2, -2)}</strong>
    if (p.startsWith('*') && p.endsWith('*') && p.length > 2) return <em key={k}>{p.slice(1, -1)}</em>
    return p
  })
}

const CODE = { fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace', fontSize: '0.92em', background: 'var(--surface-2, rgba(127,127,127,.12))', padding: '1px 4px', borderRadius: 3 }
const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())

export default function MiniMarkdown({ text }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  const out = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (line.startsWith('```')) {
      const body = []
      for (i++; i < lines.length && !lines[i].startsWith('```'); i++) body.push(lines[i])
      i++
      out.push(<pre key={out.length} style={{ ...CODE, display: 'block', padding: '8px 10px', overflowX: 'auto', margin: '8px 0', whiteSpace: 'pre' }}>{body.join('\n')}</pre>)
      continue
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    if (h) {
      const Tag = `h${Math.min(h[1].length + 1, 5)}`
      out.push(<Tag key={out.length} style={{ margin: h[1].length === 1 ? '0 0 10px' : '18px 0 8px', fontSize: [20, 16, 14, 13][h[1].length - 1], textWrap: 'balance' }}>{inline(h[2])}</Tag>)
      i++
      continue
    }
    if (/^-{3,}\s*$/.test(line)) {
      out.push(<hr key={out.length} style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '14px 0' }} />)
      i++
      continue
    }
    if (line.trim().startsWith('|') && i + 1 < lines.length && /^\s*\|?\s*:?-+/.test(lines[i + 1])) {
      const head = cells(line)
      const rows = []
      for (i += 2; i < lines.length && lines[i].trim().startsWith('|'); i++) rows.push(cells(lines[i]))
      out.push(
        <div key={out.length} style={{ overflowX: 'auto', margin: '8px 0' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 12.5, width: '100%' }}>
            <thead><tr>{head.map((c, j) => <th key={j} style={{ textAlign: 'left', padding: '5px 8px', borderBottom: '1px solid var(--border)', color: 'var(--text-dim)', fontWeight: 700 }}>{inline(c)}</th>)}</tr></thead>
            <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, j) => <td key={j} style={{ padding: '5px 8px', borderBottom: '1px solid var(--border)', verticalAlign: 'top' }}>{inline(c, `${ri}-${j}-`)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      )
      continue
    }
    const listRe = /^(\s*)([-*]|\d+\.)\s+(.*)$/
    if (listRe.test(line)) {
      const ordered = /^\s*\d+\./.test(line)
      const items = []
      while (i < lines.length && (listRe.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        const m = listRe.exec(lines[i])
        if (m) items.push(m[3])
        else items[items.length - 1] += ` ${lines[i].trim()}`
        i++
      }
      const Tag = ordered ? 'ol' : 'ul'
      out.push(<Tag key={out.length} style={{ margin: '6px 0', paddingLeft: 22, lineHeight: 1.55 }}>{items.map((t, j) => <li key={j}>{inline(t, `${j}-`)}</li>)}</Tag>)
      continue
    }
    if (line.startsWith('>')) {
      const body = []
      for (; i < lines.length && lines[i].startsWith('>'); i++) body.push(lines[i].replace(/^>\s?/, ''))
      out.push(<blockquote key={out.length} style={{ margin: '8px 0', padding: '4px 12px', borderLeft: '3px solid var(--border)', color: 'var(--text-dim)' }}>{inline(body.join(' '))}</blockquote>)
      continue
    }
    if (!line.trim()) { i++; continue }
    const para = []
    for (; i < lines.length && lines[i].trim() && !/^(#|```|\||>|-{3,}\s*$|\s*([-*]|\d+\.)\s)/.test(lines[i]); i++) para.push(lines[i].trim())
    if (!para.length) { para.push(line.trim()); i++ }
    out.push(<p key={out.length} style={{ margin: '6px 0', lineHeight: 1.55, maxWidth: '68ch' }}>{inline(para.join(' '))}</p>)
  }
  return <div data-mini-markdown style={{ fontSize: 13 }}>{out}</div>
}
