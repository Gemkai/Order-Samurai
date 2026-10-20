import { execFile } from "node:child_process"
import path from "node:path"
import { fileURLToPath } from "node:url"
import type { Connect, Plugin } from "vite"
import { isVersionStatus } from "./src/lib/version"
import type { VersionStatus } from "./src/types"

const UNKNOWN: VersionStatus = { current_version: null, latest_version: null, update_available: false }

export default function versionPlugin(): Plugin {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
  // Cache the in-flight request too, so opening multiple tabs starts only one child.
  let cached: Promise<VersionStatus> | undefined
  const middleware: Connect.NextHandleFunction = (req, res, next) => {
    if (req.method !== "GET" || req.url?.split("?")[0] !== "/api/version") {
      next()
      return
    }
    cached ??= new Promise<VersionStatus>((resolve) => {
      execFile("python3", [path.join(root, "bin", "samurai"), "version", "--json"],
        { timeout: 8_000, killSignal: "SIGKILL", maxBuffer: 64 * 1024, encoding: "utf8" }, (error, stdout) => {
          if (error) { resolve(UNKNOWN); return }
          try {
            const value: unknown = JSON.parse(stdout)
            resolve(isVersionStatus(value) ? value : UNKNOWN)
          } catch { resolve(UNKNOWN) }
        })
    }).catch(() => UNKNOWN)
    void cached.then((status) => {
      res.statusCode = 200
      res.setHeader("Content-Type", "application/json")
      res.end(JSON.stringify(status))
    })
  }
  return {
    name: "samurai-version",
    // Register directly: returning a hook would place this after Vite's API proxy.
    configureServer(server) { server.middlewares.use(middleware) },
    configurePreviewServer(server) { server.middlewares.use(middleware) },
  }
}
