import { useState as useLocalState } from 'react'
import type { DojoProps, PillarSlug } from '@/hooks/useDojo'

function summarizeOutput(lines: string[]): string {
  const raw = lines.join('\n')
  const stripped = raw
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*/g, '')
    .replace(/\*/g, '')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/^[-─=]{3,}$/gm, '')
    .replace(/^\s*[-*•]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/^>\s*/gm, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')

  const sentences = stripped
    .split(/(?<=[.!?])\s+|\n+/)
    .map(s => s.trim())
    .filter(s =>
      s.length > 25 &&
      !/^(Error|Warning|Traceback|at |Stack trace|✓|✗|⚡|Hook|Running|Loaded|\[|\])/.test(s)
    )

  if (!sentences.length) {
    const fallback = lines.filter(l => l.trim().length > 15).slice(-1)[0]?.trim() ?? ''
    return fallback
  }

  const summary = sentences.slice(0, 4).join(' ').replace(/\s+/g, ' ')
  return summary.endsWith('.') || summary.endsWith('!') || summary.endsWith('?') ? summary : summary + '.'
}

interface DojoPanelProps {
  pillar: PillarSlug
  dojoProps: DojoProps
  /** When true, renders without the top-border separator — for embedding inside the summary pill. */
  inline?: boolean
}

export function DojoPanel({ pillar, dojoProps, inline = false }: DojoPanelProps) {
  const [expanded, setExpanded] = useLocalState(false)
  const { dojoState, roninStatus, roninOutput, connected, anyRunning, toggle, run } = dojoProps
  const slug = pillar
  const ds = dojoState?.pillars[slug]
  const status = roninStatus[slug] ?? 'idle'
  const output = roninOutput[slug] ?? []
  const on = ds?.ronin_mode === 'ronin'
  const isRunning = status === 'running'
  // Block the button when ANY pillar cycle is running — manual cycles share the git
  // working tree, so concurrent runs would race. The server queues them automatically,
  // but we surface that in the UI so you know why the button is locked.
  const isCycleBlocked = !connected || anyRunning

  return (
    <div style={inline ? {} : { marginTop: 14, paddingTop: 12, borderTop: '1px solid rgba(255,255,255,0.07)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>

        {/* Ronin toggle button */}
        <button
          onClick={(e) => { e.stopPropagation(); if (connected) toggle(slug) }}
          disabled={!connected}
          title={connected ? (on ? 'Click to disable ronin mode' : 'Click to enable ronin mode') : 'API offline — start npm run dev in Governance/api/'}
          style={{
            background: on ? 'rgba(239,68,68,0.14)' : 'rgba(255,255,255,0.05)',
            border: `1px solid ${on ? 'var(--sword)' : 'rgba(255,255,255,0.12)'}`,
            borderRadius: 6,
            padding: '4px 12px',
            cursor: connected ? 'pointer' : 'not-allowed',
            color: on ? 'var(--sword)' : 'var(--muted-foreground)',
            transition: 'all 0.15s',
          }}
        >
          <span className="mono" style={{ fontSize: 'var(--text-caption)', letterSpacing: 1, fontWeight: on ? 700 : 400 }}>
            {on ? '◉ RONIN ARMED' : '○ DORMANT'}
          </span>
        </button>

        {/* Run cycle button */}
        <button
          onClick={(e) => { e.stopPropagation(); if (!isCycleBlocked) run(slug) }}
          disabled={isCycleBlocked}
          title={
            !connected
              ? 'API offline — start npm run dev in Governance/api/'
              : isRunning
              ? '⚡ Cycle in progress…'
              : anyRunning
              ? 'Another pillar is running — click to queue (will auto-run when done)'
              : 'Launch dojo cycle for this pillar'
          }
          style={{
            background: isRunning ? 'rgba(34,197,94,0.12)' : anyRunning && !isRunning ? 'rgba(251,191,36,0.08)' : 'rgba(255,255,255,0.04)',
            border: `1px solid ${isRunning ? 'var(--bow)' : anyRunning && !isRunning ? 'rgba(251,191,36,0.35)' : 'rgba(255,255,255,0.09)'}`,
            borderRadius: 6,
            padding: '4px 12px',
            cursor: isCycleBlocked ? 'not-allowed' : 'pointer',
            color: isRunning ? 'var(--bow)' : anyRunning && !isRunning ? 'rgba(251,191,36,0.6)' : 'rgba(255,255,255,0.35)',
            transition: 'all 0.15s',
          }}
        >
          <span className="mono" style={{ fontSize: 'var(--text-caption)', letterSpacing: 1 }}>
            {isRunning ? '⚡ RUNNING…' : status === 'done' ? '✓ DONE' : status === 'error' ? '✗ ERROR' : '⚡ MEDITATION CYCLE'}
          </span>
        </button>

        {/* Live / baseline ratio */}
        {ds && (
          <span className="mono" style={{ fontSize: 'var(--text-caption)', color: 'var(--muted-foreground)', marginLeft: 'auto' }}>
            {ds.live_current ?? '—'}/{ds.live_baseline}
          </span>
        )}

        {/* API offline indicator */}
        {!connected && (
          <span className="mono" style={{ fontSize: 'var(--text-caption)', color: 'rgba(255,255,255,0.22)', marginLeft: ds ? 0 : 'auto' }}>
            api offline
          </span>
        )}
      </div>

      {/* Skill output — summarized by default, expandable */}
      {output.length > 0 && (
        <div style={{ marginTop: 8 }}>
          {expanded ? (
            <div style={{
              background: 'rgba(0,0,0,0.38)',
              border: '1px solid rgba(255,255,255,0.06)',
              borderRadius: 8,
              padding: '7px 10px',
              maxHeight: 180,
              overflowY: 'auto',
            }}>
              {output.map((line, i) => (
                <div key={i} style={{ fontSize: 'var(--text-caption)', color: 'rgba(255,255,255,0.6)', lineHeight: 1.65, fontFamily: 'JetBrains Mono, monospace' }}>
                  {line}
                </div>
              ))}
            </div>
          ) : (
            <div style={{
              background: 'rgba(0,0,0,0.25)',
              border: '1px solid rgba(255,255,255,0.06)',
              borderRadius: 8,
              padding: '7px 10px',
            }}>
              <p style={{ margin: 0, fontSize: 'var(--text-caption)', color: 'rgba(255,255,255,0.72)', lineHeight: 1.6 }}>
                {summarizeOutput(output)}
              </p>
            </div>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); setExpanded(x => !x) }}
            style={{
              marginTop: 4,
              background: 'none',
              border: 'none',
              padding: '2px 0',
              cursor: 'pointer',
              color: 'rgba(255,255,255,0.28)',
              fontSize: 'var(--text-caption)',
              fontFamily: 'JetBrains Mono, monospace',
              letterSpacing: 0.5,
            }}
          >
            {expanded ? '↑ collapse' : '↓ full output'}
          </button>
        </div>
      )}
    </div>
  )
}
