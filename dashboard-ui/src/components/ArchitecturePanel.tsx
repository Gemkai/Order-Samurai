import { useState } from "react"
import { ChevronRight } from "lucide-react"
import type { ArchitectureBreakdown, ArchitectureCategory } from "@/types"

// Status encoding: color + a distinct glyph (shape), never color alone — colorblind-safe.
// advisory_gap = a category with no verifier yet (untargeted), so it reads neutral, NOT as a
// hard failure; blocking (a verifier actively FAILing) is the only red.
const STATUS: Record<string, { color: string; glyph: string; label: string }> = {
  pass:          { color: "#4ade80",                glyph: "✓", label: "Pass" },
  advisory_warn: { color: "var(--bow)",             glyph: "▲", label: "Advisory warn" },
  advisory_gap:  { color: "var(--muted-foreground)", glyph: "○", label: "Advisory gap (no verifier)" },
  blocking:      { color: "var(--sword)",           glyph: "✕", label: "Blocking" },
}
const statusOf = (s: string) => STATUS[s] ?? { color: "var(--muted-foreground)", glyph: "·", label: s }

function CategoryCell({ c }: { c: ArchitectureCategory }) {
  const st = statusOf(c.status)
  const detail = c.missing_verifiers.length
    ? `Missing: ${c.missing_verifiers.join(", ")}`
    : c.warnings.length
      ? (c.warnings[0]?.detail ?? "Warning")
      : `${st.label} · ${c.earned}/${c.weight} pts earned`
  return (
    <div data-tip={detail}
      style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", borderRadius: 10,
        background: "rgba(0,0,0,0.22)", border: `1px solid ${st.color}26`, cursor: "default" }}>
      <span aria-hidden style={{ color: st.color, fontSize: "0.85rem", fontWeight: 700, width: 14, textAlign: "center", flexShrink: 0 }}>{st.glyph}</span>
      <span className="mono" style={{ fontSize: "0.66rem", fontWeight: 600, flex: 1, minWidth: 0,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.label}</span>
      <span className="mono" style={{ fontSize: "var(--text-caption)", color: st.color, letterSpacing: 0.5,
        textTransform: "uppercase", whiteSpace: "nowrap" }}>{st.label.split(" ")[0]}</span>
      <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", minWidth: 36, textAlign: "right" }}>{c.earned}/{c.weight}</span>
    </div>
  )
}

/** Architecture decomposition: per-category status is the headline; the 0–100 score is demoted
 *  to a collapsed, click-to-expand figure (kept for history, never the anchor). */
export function ArchitecturePanel({ arch }: { arch: ArchitectureBreakdown | null | undefined }) {
  const [open, setOpen] = useState(false)
  if (!arch) {
    return (
      <div className="glass" style={{ borderRadius: 18, padding: "18px 22px", borderLeft: "3px solid var(--brush)" }}>
        <div className="mono" style={{ fontSize: "0.72rem", letterSpacing: 1.5, textTransform: "uppercase", color: "var(--brush)", marginBottom: 6 }}>Architecture Scorecard</div>
        <div className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>— no scorecard data (run the architecture scorer)</div>
      </div>
    )
  }
  const cats = arch.categories ?? []
  const tally = cats.reduce<Record<string, number>>((a, c) => { a[c.status] = (a[c.status] ?? 0) + 1; return a }, {})
  const summary = (["blocking", "advisory_gap", "advisory_warn", "pass"] as const)
    .filter((s) => tally[s]).map((s) => `${tally[s]} ${statusOf(s).label.split(" ")[0].toLowerCase()}`)
    .join(" · ")

  return (
    <div className="glass" style={{ borderRadius: 18, padding: "18px 22px", borderLeft: "3px solid var(--brush)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14, flexWrap: "wrap" }}>
        <div className="mono" style={{ fontSize: "0.72rem", letterSpacing: 1.5, textTransform: "uppercase", color: "var(--brush)" }}>Architecture Scorecard</div>
        <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>{summary || "no categories"}</span>
        {/* Score demoted to a collapsed figure — click to expand floors/enforcement. */}
        <button onClick={() => setOpen((v) => !v)} className="mono"
          data-tip="Show/hide the composite score and merge/release floors"
          style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 5, cursor: "pointer",
            fontSize: "var(--text-caption)", letterSpacing: 1, textTransform: "uppercase", padding: "4px 9px", borderRadius: 7,
            color: "var(--muted-foreground)", background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)" }}>
          <ChevronRight size={12} style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform 0.2s" }} />
          score {arch.score ?? "—"}{arch.target_score ? `/${arch.target_score}` : ""}
        </button>
      </div>

      {/* Primary view: per-category status grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 8 }}>
        {cats.length === 0
          ? <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)" }}>—</span>
          : cats.map((c) => <CategoryCell key={c.id} c={c} />)}
      </div>

      {/* Secondary, collapsed: the demoted composite score + floors */}
      {open && (
        <div style={{ marginTop: 14, padding: "12px 14px", borderRadius: 12, background: "rgba(0,0,0,0.25)",
          border: "1px solid rgba(255,255,255,0.06)", display: "flex", flexWrap: "wrap", gap: 18, alignItems: "baseline" }}>
          <span className="mono" style={{ fontSize: "1.6rem", fontWeight: 700, color: "var(--brush)" }}>
            {arch.score ?? "—"}<span style={{ fontSize: "0.7rem", color: "var(--muted-foreground)" }}>/{arch.target_score ?? 100}</span>
          </span>
          <div className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", lineHeight: 1.7 }}>
            <div>merge floor {arch.merge_floor ?? "—"} · {arch.meets_merge_floor ? "met" : "not met"}</div>
            <div>release floor {arch.release_floor ?? "—"} · {arch.meets_release_floor ? "met" : "not met"}</div>
            {arch.enforcement_mode && <div>mode: {arch.enforcement_mode}</div>}
          </div>
          {arch.advisory_gaps.length > 0 && (
            <div className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", maxWidth: 320 }}>
              <span style={{ color: "var(--bow)" }}>advisory gaps:</span> {arch.advisory_gaps.join(", ")}
            </div>
          )}
          <span className="mono" style={{ fontSize: "var(--text-caption)", color: "var(--muted-foreground)", marginLeft: "auto" }}>
            This composite is a conversation-starter, not a target. Per-category status above is the real signal.
          </span>
        </div>
      )}
    </div>
  )
}
