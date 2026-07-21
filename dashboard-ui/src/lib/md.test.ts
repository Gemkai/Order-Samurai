import { describe, it, expect } from "vitest"
import { mdToHtml } from "./md"

describe("mdToHtml", () => {
  it("renders headings", () => {
    expect(mdToHtml("# Title")).toContain("<h1>Title</h1>")
    expect(mdToHtml("## Sub")).toContain("<h2>Sub</h2>")
  })

  it("renders a table", () => {
    const html = mdToHtml("| A | B |\n| --- | --- |\n| 1 | 2 |")
    expect(html).toContain("<table>")
    expect(html).toContain("<th>A</th>")
    expect(html).toContain("<td>1</td>")
  })

  it("renders blockquote and bold/italic/code inline", () => {
    expect(mdToHtml("> note")).toContain("<blockquote>note</blockquote>")
    expect(mdToHtml("**b**")).toContain("<strong>b</strong>")
    expect(mdToHtml("`c`")).toContain("<code>c</code>")
    expect(mdToHtml("_Generated now_ x")).toContain("<em>Generated now</em>")
  })

  it("renders the {{c:COLOR::TEXT}} color token as a span", () => {
    const html = mdToHtml("| P | S | {{c:var(--bow)::████}} |\n| - | - | - |\n| a | b | c |")
    expect(html).toContain('style="color:var(--bow)')
    expect(html).toContain("████")
  })

  it("merges wrapped paragraph lines so inline spans survive line breaks", () => {
    const html = mdToHtml("**runtime\nwide**")
    expect(html).toContain("<strong>runtime wide</strong>")
  })

  it("escapes raw html", () => {
    expect(mdToHtml("<script>")).toContain("&lt;script&gt;")
  })

  it("passes inline SVG through verbatim", () => {
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><line x1="0" y1="0" x2="10" y2="10"/></svg>'
    const html = mdToHtml(`## Chart\n${svg}`)
    expect(html).toContain("<svg")
    expect(html).toContain("</svg>")
    expect(html).not.toContain("&lt;svg")
  })
})
