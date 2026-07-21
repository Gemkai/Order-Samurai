import { useState } from "react"
import type { CSSProperties } from "react"

interface ParticleSpec {
  symbols: string[]      // one picked at random per particle (mixed-glyph seasons)
  colors: string[]
  count: number
  sizes: [number, number]
  durations: [number, number]
  opacity: [number, number]
  particleClass?: string
  swayRange?: number
  spin?: number          // total rotation in deg: snow drifts (~70), leaves/petals tumble (~400)
}

const PARTICLES: Record<string, ParticleSpec> = {
  overview: {
    symbols: ["│"],
    colors: ["#bae6fd", "#7dd3fc", "#93c5fd", "#e0f2fe", "#38bdf8"],
    count: 14,
    sizes: [10, 18],
    durations: [3, 7],
    opacity: [0.22, 0.52],
    particleClass: "sidebar-particle-rain",
    swayRange: 10,
  },
  bow: {
    // autumn leaves — amber only, tumbling
    symbols: ["🍁", "🍂"],
    colors: ["#f59e0b", "#d97706", "#b45309", "#fbbf24", "#92400e"],
    count: 24,
    sizes: [16, 28],
    durations: [12, 24],
    opacity: [0.3, 0.55],
    swayRange: 70,
    spin: 420,
  },
  sword: {
    // red autumn leaves — tumbling
    symbols: ["🍁", "🍂"],
    colors: ["#ef4444", "#dc2626", "#b91c1c", "#f87171", "#fca5a5"],
    count: 24,
    sizes: [16, 28],
    durations: [12, 24],
    opacity: [0.3, 0.55],
    swayRange: 70,
    spin: 420,
  },
  brush: {
    // cherry-blossom petals — pink, soft spin
    symbols: ["✿", "❀", "❁"],
    colors: ["#f472b6", "#f9a8d4", "#fce7f3", "#ec4899", "#fbcfe8"],
    count: 24,
    sizes: [12, 22],
    durations: [12, 24],
    opacity: [0.28, 0.55],
    swayRange: 60,
    spin: 360,
  },
  arts: {
    // winter snow — white, slow gentle drift
    symbols: ["❄", "❅", "❆"],
    colors: ["#ffffff", "#f1f5f9", "#e2e8f0", "#f8fafc"],
    count: 24,
    sizes: [11, 20],
    durations: [12, 24],
    opacity: [0.24, 0.55],
    swayRange: 32,
    spin: 70,
  },
}

export function SidebarParticles({ pillar }: { pillar: string }) {
  const spec = PARTICLES[pillar] ?? null

  // Negative delays seed each particle mid-animation so they don't all start at top.
  // useState lazy initializer avoids Math.random() during render (react-hooks/purity).
  // Parent must pass key={pillar} to remount when pillar changes, resetting this state.
  const [particles] = useState(() => {
    if (!spec) return []
    return Array.from({ length: spec.count }, (_, i) => {
      const dur = spec.durations[0] + Math.random() * (spec.durations[1] - spec.durations[0])
      return {
        id: i,
        left: 5 + Math.random() * 80,
        size: spec.sizes[0] + Math.random() * (spec.sizes[1] - spec.sizes[0]),
        duration: dur,
        delay: -(Math.random() * dur),
        opacity: spec.opacity[0] + Math.random() * (spec.opacity[1] - spec.opacity[0]),
        color: spec.colors[Math.floor(Math.random() * spec.colors.length)],
        sway: (Math.random() - 0.5) * (spec.swayRange ?? 65),
        spin: spec.spin ?? 360,
        symbol: spec.symbols[Math.floor(Math.random() * spec.symbols.length)] || "",
      }
    })
  })

  if (!spec) return null

  return (
    <div
      aria-hidden="true"
      style={{
        position: "absolute",
        inset: 0,
        pointerEvents: "none",
        overflow: "hidden",
        zIndex: 0,
      }}
    >
      {particles.map((p) => (
        <span
          key={p.id}
          className={spec.particleClass ?? "sidebar-particle"}
          style={{
            left: `${p.left}%`,
            top: "-16px",
            fontSize: `${p.size}px`,
            color: p.color,
            animationDuration: `${p.duration}s`,
            animationDelay: `${p.delay}s`,
            "--p-sway": `${p.sway}px`,
            "--p-spin": `${p.spin}deg`,
            "--p-opacity": `${p.opacity}`,
          } as CSSProperties}
        >
          {p.symbol}
        </span>
      ))}
    </div>
  )
}
