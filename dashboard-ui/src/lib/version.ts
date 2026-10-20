import type { VersionStatus } from "../types"

export async function fetchVersionStatus(): Promise<VersionStatus | undefined> {
  try {
    const response = await fetch("/api/version", { signal: AbortSignal.timeout(10_000) })
    if (!response.ok) return undefined
    const value: unknown = await response.json()
    return isVersionStatus(value) ? resolveVersionStatus(value, undefined) : undefined
  } catch {
    return undefined
  }
}

export function resolveVersionStatus(live: VersionStatus | undefined, payload: VersionStatus | undefined): VersionStatus | undefined {
  return live?.current_version != null && live.latest_version != null ? live : payload
}

export function isVersionStatus(value: unknown): value is VersionStatus {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false
  const status = value as Record<string, unknown>
  return Object.keys(status).length === 3
    && (typeof status.current_version === "string" || status.current_version === null)
    && (typeof status.latest_version === "string" || status.latest_version === null)
    && typeof status.update_available === "boolean"
}
