import { useState } from "react"

import type { VersionStatus } from "@/types"

export const UPDATE_COMMAND = "curl -fsSL https://www.ordersamurai.ai/install.sh | bash"

/** Update-available banner, the same for Free and Pro. Modeled on the stale-data banner.
 *  Hidden when up to date, when the version is unknown (offline or an older payload),
 *  and in the demo, whose static payload has no install to update. */
export function UpdateBanner({ version, isDemo }: { version?: VersionStatus; isDemo: boolean }) {
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle")
  if (isDemo || !version?.update_available || !version.latest_version) return null
  const command = UPDATE_COMMAND  // never taken from the payload: it is pasted into a shell
  // The Clipboard API is missing off secure origins (dashboard opened by LAN IP) and can be
  // denied; fall back to a hidden textarea + execCommand before reporting failure.
  const legacyCopy = () => {
    const el = document.createElement("textarea")
    el.value = command
    el.setAttribute("readonly", "")
    el.style.position = "fixed"
    el.style.opacity = "0"
    document.body.appendChild(el)
    el.select()
    let ok: boolean
    try { ok = document.execCommand("copy") } catch { ok = false }
    el.remove()
    setCopy(ok ? "copied" : "failed")
  }
  const onCopy = () => {
    if (!navigator.clipboard?.writeText) { legacyCopy(); return }
    navigator.clipboard.writeText(command).then(() => setCopy("copied"), legacyCopy)
  }
  return (
    <div className="mono" role="status" style={{
      marginBottom: 16, padding: "11px 16px", borderRadius: 8,
      border: "1px solid var(--bow)", background: "rgba(250,204,21,0.07)",
      color: "var(--bow)", fontSize: "0.72rem", display: "flex",
      alignItems: "center", gap: 12, flexWrap: "wrap",
    }}>
      <span style={{ fontWeight: 700, letterSpacing: 1.5 }}>⬆ UPDATE AVAILABLE</span>
      <span style={{ color: "rgba(255,255,255,0.78)" }}>
        Order Samurai v{version.latest_version} is out (you have v{version.current_version ?? "unknown"}).
        Rerun the installer to update; your license and settings are kept.
      </span>
      <code style={{ color: "rgba(255,255,255,0.9)", userSelect: "all" }}>{command}</code>
      <button type="button" onClick={onCopy} className="mono" style={{
        padding: "4px 10px", borderRadius: 6, border: "1px solid var(--bow)",
        background: "transparent", color: "var(--bow)", cursor: "pointer", fontSize: "0.68rem",
      }}>{copy === "copied" ? "Copied" : "Copy update command"}</button>
      {copy === "failed" && (
        <span style={{ color: "rgba(255,255,255,0.6)" }}>Copy failed; select the command above.</span>
      )}
    </div>
  )
}
