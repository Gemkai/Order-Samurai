import { spawn } from 'child_process'
import fs from 'fs'
import path from 'path'
import crypto from 'crypto'
import { ORDER_SAMURAI_ROOT, GOVERNANCE_ROOT } from './state.js'
import type { RepoAuditRecord } from './types.js'

const STATE_FILE = path.join(ORDER_SAMURAI_ROOT, 'state', 'repo_audits.json')
const SANDBOX_BASE = path.join(ORDER_SAMURAI_ROOT, '.tmp', 'sandbox_audits')

export class RepoAuditManager {
  private records: Map<string, RepoAuditRecord> = new Map()
  private onUpdateCallback?: (record: RepoAuditRecord) => void

  constructor(onUpdate?: (record: RepoAuditRecord) => void) {
    this.onUpdateCallback = onUpdate
    this.loadState()
  }

  public setOnUpdate(cb: (record: RepoAuditRecord) => void) {
    this.onUpdateCallback = cb
  }

  private loadState() {
    try {
      if (fs.existsSync(STATE_FILE)) {
        const raw = fs.readFileSync(STATE_FILE, 'utf8')
        const items: RepoAuditRecord[] = JSON.parse(raw)
        items.forEach((item) => this.records.set(item.id, item))
      }
    } catch {
      // Best effort load
    }
  }

  private saveState() {
    try {
      const dir = path.dirname(STATE_FILE)
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true })
      const list = Array.from(this.records.values()).sort(
        (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
      )
      fs.writeFileSync(STATE_FILE, JSON.stringify(list, null, 2), 'utf8')
    } catch {
      // Best effort save
    }
  }

  public getAll(): RepoAuditRecord[] {
    return Array.from(this.records.values()).sort(
      (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
    )
  }

  public getById(id: string): RepoAuditRecord | undefined {
    return this.records.get(id)
  }

  public validateUrl(url: string): boolean {
    const trimmed = url.trim()
    // Flexible URL validation: must start with http(s):// or git@
    return /^https?:\/\/[^\s/$.?#].[^\s]*$/i.test(trimmed) || /^git@[^\s:]+:[^\s]+$/i.test(trimmed)
  }

  public async startAudit(repoUrl: string): Promise<RepoAuditRecord> {
    const cleanUrl = repoUrl.trim()
    if (!this.validateUrl(cleanUrl)) {
      throw new Error('Invalid repository URL format. Use a public https://github.com/owner/repo URL.')
    }

    const repoName = cleanUrl.split('/').pop()?.replace(/\.git$/, '') || 'repository'
    const auditId = `audit_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`

    const record: RepoAuditRecord = {
      id: auditId,
      repoUrl: cleanUrl,
      repoName,
      status: 'pending',
      timestamp: new Date().toISOString(),
      summary: { critical: 0, high: 0, medium: 0, low: 0 },
    }

    this.records.set(auditId, record)
    this.saveState()
    this.notify(record)

    // Run async audit pipeline in background
    this.executeAuditPipeline(auditId, cleanUrl).catch((err) => {
      console.error(`[repo-audit] Error executing pipeline for ${auditId}:`, err)
    })

    return record
  }

  private notify(record: RepoAuditRecord) {
    if (this.onUpdateCallback) {
      this.onUpdateCallback(record)
    }
  }

  private async executeAuditPipeline(auditId: string, repoUrl: string) {
    const record = this.records.get(auditId)
    if (!record) return

    const sandboxDir = path.join(SANDBOX_BASE, auditId)

    try {
      // 1. Update status -> cloning
      record.status = 'cloning'
      this.notify(record)

      if (!fs.existsSync(SANDBOX_BASE)) fs.mkdirSync(SANDBOX_BASE, { recursive: true })

      // Clone repository safely using strict argument array (prevent CLI injection)
      await new Promise<void>((resolve, reject) => {
        const gitProc = spawn('git', ['clone', '--depth', '1', '--', repoUrl, sandboxDir], {
          cwd: SANDBOX_BASE,
          stdio: ['ignore', 'pipe', 'pipe'],
        })

        let errOutput = ''
        gitProc.stderr.on('data', (data) => { errOutput += data.toString() })
        gitProc.on('close', (code) => {
          if (code === 0) resolve()
          else reject(new Error(`Git clone failed with code ${code}: ${errOutput.slice(0, 300)}`))
        })
        gitProc.on('error', (err) => reject(err))
      })

      // 2. Update status -> auditing
      record.status = 'auditing'
      this.notify(record)

      const jsonOutPath = path.join(sandboxDir, 'audit_report.json')
      const mdOutPath = path.join(sandboxDir, 'audit_report.md')
      const scriptPath = path.join(GOVERNANCE_ROOT, 'execution', 'repo_auditor.py')

      const pythonBin = process.platform === 'win32' ? 'python' : 'python3'

      await new Promise<void>((resolve, reject) => {
        const auditProc = spawn(
          pythonBin,
          [
            scriptPath,
            '--target-dir', sandboxDir,
            '--repo-url', repoUrl,
            '--output-json', jsonOutPath,
            '--output-md', mdOutPath,
          ],
          {
            cwd: GOVERNANCE_ROOT,
            stdio: ['ignore', 'pipe', 'pipe'],
          }
        )

        let stdout = ''
        let stderr = ''
        auditProc.stdout.on('data', (d) => { stdout += d.toString() })
        auditProc.stderr.on('data', (d) => { stderr += d.toString() })

        auditProc.on('close', (code) => {
          if (code === 0) {
            try {
              if (fs.existsSync(jsonOutPath)) {
                const parsed = JSON.parse(fs.readFileSync(jsonOutPath, 'utf8'))
                record.summary = parsed.summary || { critical: 0, high: 0, medium: 0, low: 0 }
                record.reportMarkdown = parsed.report_markdown || fs.readFileSync(mdOutPath, 'utf8')
              }
              resolve()
            } catch (err: any) {
              reject(new Error(`Failed to parse audit results: ${err.message}`))
            }
          } else {
            reject(new Error(`Audit script failed with code ${code}: ${stderr.slice(0, 300)}`))
          }
        })
        auditProc.on('error', (err) => reject(err))
      })

      // 3. Update status -> completed
      record.status = 'completed'
      this.saveState()
      this.notify(record)
    } catch (err: any) {
      record.status = 'failed'
      record.error = err.message || 'Audit execution failed'
      this.saveState()
      this.notify(record)
    } finally {
      // Cleanup sandbox dir asynchronously to prevent disk bloat
      try {
        if (fs.existsSync(sandboxDir)) {
          fs.rmSync(sandboxDir, { recursive: true, force: true })
        }
      } catch {
        // Best-effort cleanup
      }
    }
  }
}
