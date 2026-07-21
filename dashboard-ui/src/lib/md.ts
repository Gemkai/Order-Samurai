// Minimal markdown -> html for the trusted, self-generated weekly reports.
// Supports headings, blockquotes, tables, bold/italic/code, the {{c:COLOR::TEXT}}
// token used for pillar-tinted radar bars, and inline SVG (passed through verbatim).
export function mdToHtml(md: string): string {
  // Extract SVG blocks before escaping so inline chart SVGs survive verbatim
  const svgBlocks: string[] = []
  const src = md.replace(/<svg[\s\S]*?<\/svg>/gi, (m) => {
    svgBlocks.push(m)
    return `\x00SVG${svgBlocks.length - 1}\x00`
  })

  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  const inline = (s: string) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/(^|\s)_(.+?)_(?=\s|$|\.)/g, "$1<em>$2</em>")
    .replace(/\{\{c:([a-z0-9(),\-#. ]+)::([^}]+)\}\}/gi,
      '<span style="color:$1;font-family:JetBrains Mono Variable,monospace;letter-spacing:-1px">$2</span>')
  const lines = src.split("\n")
  const out: string[] = []
  let i = 0
  while (i < lines.length) {
    const ln = lines[i]
    if (/^\|/.test(ln)) {
      const rows: string[] = []
      while (i < lines.length && /^\|/.test(lines[i])) { rows.push(lines[i]); i++ }
      const cells = (r: string) => r.split("|").slice(1, -1).map((c) => c.trim())
      const head = cells(rows[0])
      const body = rows.slice(2) // skip --- separator
      out.push("<table><thead><tr>" + head.map((h) => `<th>${inline(h)}</th>`).join("") + "</tr></thead><tbody>"
        + body.map((r) => "<tr>" + cells(r).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table>")
      continue
    }
    if (/^### /.test(ln)) { out.push(`<h3>${inline(ln.slice(4))}</h3>`); i++ }
    else if (/^## /.test(ln)) { out.push(`<h2>${inline(ln.slice(3))}</h2>`); i++ }
    else if (/^# /.test(ln)) { out.push(`<h1>${inline(ln.slice(2))}</h1>`); i++ }
    else if (/^> /.test(ln)) {
      const q: string[] = []
      while (i < lines.length && /^> ?/.test(lines[i])) { q.push(lines[i].replace(/^> ?/, "")); i++ }
      out.push(`<blockquote>${inline(q.join(" "))}</blockquote>`); continue
    }
    else if (ln.trim() === "") { out.push(""); i++ }
    else {
      const para: string[] = []
      while (i < lines.length && lines[i].trim() !== "" && !/^[#>|]/.test(lines[i])) { para.push(lines[i]); i++ }
      out.push(`<p>${inline(para.join(" "))}</p>`)
    }
  }
  const html = out.join("\n")
  if (!svgBlocks.length) return html
  return svgBlocks.reduce(
    (acc, block, i) => acc.split(`\x00SVG${i}\x00`).join(block),
    html
  )
}
