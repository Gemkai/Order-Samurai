export const TREND_COLOR: Record<string, string> = {
  up: "var(--bow)",
  down: "var(--sword)",
  neutral: "var(--muted-foreground)",
}

export const TREND_ARROW: Record<string, string> = {
  up: "\u25b2",
  down: "\u25bc",
  neutral: "\u25ac",
}

export const trendColor = (trend: string) => TREND_COLOR[trend] ?? TREND_COLOR.neutral
export const trendArrow = (trend: string) => TREND_ARROW[trend] ?? TREND_ARROW.neutral

export function tierColor(tier: string) {
  return tier === "AUTO" ? "var(--bow)" : tier === "DERIVED" ? "var(--brush)" : "var(--muted-foreground)"
}
