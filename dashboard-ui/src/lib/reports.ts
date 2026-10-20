import { mdToHtml } from "@/lib/md"

/** One row of public/reports/index.json, written by `samurai audit` / weekly_report.py. */
export interface ReportEntry {
  file: string
  kind: "audit" | "weekly"
  title: string
  generated: string
  week?: string
  platform?: string
  records?: number
}

type Fetcher = (url: string, init?: RequestInit) => Promise<Pick<Response, "ok" | "json" | "text">>

// A bare file name in the reports dir: no path separators, no traversal.
const SAFE_FILE = /^[A-Za-z0-9][A-Za-z0-9_.-]*\.md$/
const isSafe = (file: unknown): file is string => typeof file === "string" && SAFE_FILE.test(file) && !file.includes("..")
// v2.2.1 shipped an index without kind/title/generated; those rows are not this machine's.
const isEntry = (e: unknown): e is ReportEntry => {
  const r = e as ReportEntry
  return !!r && typeof r === "object" && isSafe(r.file) && (r.kind === "audit" || r.kind === "weekly")
    && typeof r.title === "string" && typeof r.generated === "string"
}

/** The report list, newest first. Empty (never an error) when no report exists yet. */
export async function loadReportIndex(fetcher: Fetcher = fetch): Promise<ReportEntry[]> {
  try {
    const res = await fetcher("reports/index.json", { cache: "no-store" })
    if (!res.ok) return []
    const data: unknown = await res.json()
    if (!Array.isArray(data)) return []
    return data.filter(isEntry)
  } catch {
    return []
  }
}

export async function loadReportHtml(file: string, fetcher: Fetcher = fetch): Promise<string> {
  if (!isSafe(file)) throw new Error(`not a report file: ${file}`)
  const res = await fetcher(`reports/${file}`, { cache: "no-store" })
  if (!res.ok) throw new Error(`failed to load ${file}`)
  return mdToHtml(await res.text())
}

/** Free includes the newest report; report history ships with Pro. */
export const isReportLocked = (index: number, isPro: boolean): boolean => index > 0 && !isPro
