import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Children, Fragment, createElement, isValidElement, type ReactNode } from 'react'
import { ReflexPanel } from './ReflexList'
import type { DojoProps } from '@/hooks/useDojo'
import type { Reflex } from '@/types'

const state = vi.hoisted(() => {
  type Frame = { path: string; slot: number }
  const values = new Map<string, unknown>()
  const frames: Frame[] = []
  return {
    reset() { values.clear(); frames.length = 0 },
    enter(path: string) { frames.push({ path, slot: 0 }) },
    leave() { frames.pop() },
    useState<T>(initial: T | (() => T)): [T, (next: T | ((prior: T) => T)) => void] {
      const frame = frames.at(-1)
      if (!frame) throw new Error('useState called outside the acceptance renderer')
      const key = `${frame.path}:hook-${frame.slot++}`
      if (!values.has(key)) values.set(key, typeof initial === 'function' ? (initial as () => T)() : initial)
      return [values.get(key) as T, (next) => {
        const prior = values.get(key) as T
        values.set(key, typeof next === 'function' ? (next as (value: T) => T)(prior) : next)
      }]
    },
  }
})

vi.mock('react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react')>()
  return { ...actual, useState: state.useState }
})

type Rendered = {
  type: string
  props: Record<string, unknown>
  children: Array<Rendered | string | number>
  parent?: Rendered
}

function expand(node: ReactNode, path = 'root', parent?: Rendered): Array<Rendered | string | number> {
  if (node === null || node === undefined || typeof node === 'boolean') return []
  if (typeof node === 'string' || typeof node === 'number') return [node]
  if (Array.isArray(node)) return node.flatMap((child, i) => expand(child, `${path}.${i}`, parent))
  if (!isValidElement(node)) return []

  const element = node as ReturnType<typeof createElement>
  const props = element.props as Record<string, unknown>
  const keyedPath = `${path}.${element.key == null ? 'unkeyed' : String(element.key)}`
  if (element.type === Fragment) return expand(props.children as ReactNode, `${keyedPath}.fragment`, parent)
  if (typeof element.type === 'function') {
    state.enter(`${keyedPath}.${element.type.name || 'component'}`)
    try {
      const Component = element.type as (componentProps: Record<string, unknown>) => ReactNode
      return expand(Component(props), `${keyedPath}.rendered`, parent)
    } finally {
      state.leave()
    }
  }
  if (typeof element.type !== 'string') return []

  const rendered: Rendered = { type: element.type, props, children: [], parent }
  rendered.children = Children.toArray(props.children as ReactNode)
    .flatMap((child, i) => expand(child, `${keyedPath}.${i}`, rendered))
  return [rendered]
}

function nodes(tree: Array<Rendered | string | number>): Rendered[] {
  const out: Rendered[] = []
  const visit = (item: Rendered | string | number) => {
    if (typeof item !== 'object') return
    out.push(item)
    item.children.forEach(visit)
  }
  tree.forEach(visit)
  return out
}
const textOf = (item: Rendered | string | number): string => typeof item === 'object'
  ? item.children.map(textOf).join(' ').replace(/\s+/g, ' ').trim()
  : String(item)

const reflex = (id: string, over: Partial<Reflex> = {}): Reflex => ({
  id, tier: 'HIGH', category: 'Operations', source: 'metric', trigger: `trigger-${id}`,
  target: `target-${id}`, message: `message-${id}`, command: `/fix-${id}`, last_fired: null,
  status: 'active', scope: `scope-${id}`, auto_remediable: true, ...over,
})

function renderPanel(reflexes: Reflex[], extra: Record<string, unknown> = {}) {
  return expand(createElement(ReflexPanel, {
    reflexes, dismissed: new Set<string>(), onDismiss: vi.fn(), onSelect: vi.fn(), ...extra,
  } as unknown as Parameters<typeof ReflexPanel>[0]))
}
const byTestId = (tree: ReturnType<typeof renderPanel>, id: string) =>
  nodes(tree).filter((node) => node.props['data-testid'] === id)
const stack = (tree: ReturnType<typeof renderPanel>, category: string) => {
  const found = byTestId(tree, 'reflex-stack').find((node) => node.props['data-category'] === category)
  expect(found, `missing ${category} reflex stack`).toBeDefined()
  return found!
}
const descendants = (root: Rendered) => nodes(root.children)
const button = (root: Rendered, label: string) => {
  const found = descendants(root).find((node) => node.type === 'button' && node.props['aria-label'] === label)
  expect(found, `missing button ${label}`).toBeDefined()
  return found!
}
function click(node: Rendered) {
  let stopped = false
  const event = { stopPropagation() { stopped = true } }
  let cursor: Rendered | undefined = node
  while (cursor) {
    const onClick = cursor.props.onClick as ((clickEvent: { stopPropagation(): void }) => void) | undefined
    if (onClick) onClick(event)
    if (stopped) break
    cursor = cursor.parent
  }
}

beforeEach(() => state.reset())

describe('public category stack navigation', () => {
  it('shows accessible previous/next controls and a count for multi-card and single-card stacks', () => {
    const tree = renderPanel([
      reflex('a'), reflex('b'), reflex('quality', { category: 'Quality' }),
    ])
    const operations = stack(tree, 'Operations')
    expect(button(operations, 'Previous reflex in Operations')).toBeDefined()
    expect(button(operations, 'Next reflex in Operations')).toBeDefined()
    expect(textOf(descendants(operations).find((node) => node.props['data-testid'] === 'reflex-stack-position')!)).toBe('1/2')

    const quality = stack(tree, 'Quality')
    expect(textOf(descendants(quality).find((node) => node.props['data-testid'] === 'reflex-stack-position')!)).toBe('1/1')
    expect(descendants(quality).filter((node) => node.type === 'button' && /reflex in Quality/.test(String(node.props['aria-label'])))).toHaveLength(0)
  })

  it('wraps both directions without selecting or dismissing a card', () => {
    const onSelect = vi.fn(), onDismiss = vi.fn()
    const items = [reflex('a'), reflex('b'), reflex('c')]
    let tree = renderPanel(items, { onSelect, onDismiss })
    click(button(stack(tree, 'Operations'), 'Previous reflex in Operations'))
    tree = renderPanel(items, { onSelect, onDismiss })
    expect(textOf(stack(tree, 'Operations'))).toContain('message-c')
    click(button(stack(tree, 'Operations'), 'Next reflex in Operations'))
    tree = renderPanel(items, { onSelect, onDismiss })
    expect(textOf(stack(tree, 'Operations'))).toContain('message-a')
    click(button(stack(tree, 'Operations'), 'Next reflex in Operations'))
    tree = renderPanel(items, { onSelect, onDismiss })
    expect(textOf(stack(tree, 'Operations'))).toContain('message-b')
    expect(onSelect).not.toHaveBeenCalled()
    expect(onDismiss).not.toHaveBeenCalled()
  })

  it('keeps the selected id across removals, then chooses the next index or clamps to the last', () => {
    const a = reflex('a'), b = reflex('b'), c = reflex('c')
    let tree = renderPanel([a, b, c])
    click(button(stack(tree, 'Operations'), 'Next reflex in Operations'))
    tree = renderPanel([b, c])
    expect(textOf(stack(tree, 'Operations'))).toContain('message-b')

    state.reset()
    tree = renderPanel([a, b, c])
    click(button(stack(tree, 'Operations'), 'Next reflex in Operations'))
    tree = renderPanel([a, c])
    expect(textOf(stack(tree, 'Operations'))).toContain('message-c')

    state.reset()
    tree = renderPanel([a, b, c])
    click(button(stack(tree, 'Operations'), 'Previous reflex in Operations'))
    tree = renderPanel([a, b])
    expect(textOf(stack(tree, 'Operations'))).toContain('message-b')
  })

  it('retains demo labeling and targets selected-card select, dismiss, and run actions', () => {
    const onSelect = vi.fn(), onDismiss = vi.fn(), exec = vi.fn()
    const dojoProps = {
      execCommand: null, execStatus: 'idle', execOutput: [], activeReflexIds: new Set<string>(),
      reflexPendingApprovals: new Map(), reflexOutput: {}, exec,
    } as unknown as DojoProps
    const items = [reflex('a'), reflex('b')]
    let tree = renderPanel(items, { onSelect, onDismiss, dojoProps, isDemo: true })
    click(button(stack(tree, 'Operations'), 'Next reflex in Operations'))
    tree = renderPanel(items, { onSelect, onDismiss, dojoProps, isDemo: true })
    const operations = stack(tree, 'Operations')
    expect(textOf(operations)).toContain('● sample')
    expect(textOf(operations)).not.toContain('● live')

    const card = descendants(operations).find((node) => typeof node.props.onClick === 'function' && textOf(node).includes('message-b'))
    expect(card).toBeDefined()
    click(card!)
    const dismiss = descendants(operations).find((node) => node.type === 'button' && node.props.title === 'dismiss reflex')
    expect(dismiss).toBeDefined()
    click(dismiss!)
    const run = descendants(operations).find((node) => node.type === 'button' && textOf(node).includes('/fix-b'))
    expect(run).toBeDefined()
    click(run!)

    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith(items[1])
    expect(onDismiss).toHaveBeenCalledWith('b')
    expect(exec).toHaveBeenCalledWith('/fix-b', 'scope-b')
  })
})
