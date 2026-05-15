import { useEffect, useMemo, useRef, useState } from 'react'
import { MessageSquare, Slash, Zap } from 'lucide-react'
import clsx from 'clsx'
import type { Conversation } from '../types'
import { fuzzyFilter } from '../lib/fuzzy'

export type PaletteAction =
  | { kind: 'new-session' }
  | { kind: 'toggle-theme' }
  | { kind: 'toggle-sidebar' }
  | { kind: 'show-shortcuts' }

export type PaletteSelection =
  | { kind: 'session'; sessionId: string }
  | { kind: 'action'; action: PaletteAction }
  | { kind: 'slash'; command: string }

interface SlashCommand {
  command: string
  description: string
}

const SLASH_COMMANDS: SlashCommand[] = [
  { command: '/stop', description: 'Stop the current generation' },
  { command: '/steer', description: 'Steer the agent mid-stream (e.g. /steer focus on…)' },
  { command: '/btw', description: 'Add an aside without affecting the response (e.g. /btw note: …)' },
]

interface ActionEntry {
  action: PaletteAction
  label: string
  shortcut?: string
}

interface Props {
  open: boolean
  conversations: Conversation[]
  theme: 'dark' | 'light'
  onClose: () => void
  onSelect: (selection: PaletteSelection) => void
}

interface FlatRow {
  group: 'sessions' | 'actions' | 'slash'
  key: string
  label: string
  hint?: string
  selection: PaletteSelection
}

export default function CommandPalette({
  open,
  conversations,
  theme,
  onClose,
  onSelect,
}: Props) {
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const actions = useMemo<ActionEntry[]>(
    () => [
      { action: { kind: 'new-session' }, label: 'New session', shortcut: 'mod+n' },
      {
        action: { kind: 'toggle-theme' },
        label: theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme',
      },
      {
        action: { kind: 'toggle-sidebar' },
        label: 'Toggle sidebar',
        shortcut: 'mod+\\',
      },
      {
        action: { kind: 'show-shortcuts' },
        label: 'Show keyboard shortcuts',
        shortcut: 'mod+/',
      },
    ],
    [theme],
  )

  const rows = useMemo<FlatRow[]>(() => {
    const sessionRows: FlatRow[] = fuzzyFilter(
      query,
      conversations,
      (c) => c.title || 'New chat',
    ).map(({ item: c }) => ({
      group: 'sessions',
      key: `session:${c.id}`,
      label: c.title || 'New chat',
      selection: { kind: 'session', sessionId: c.id },
    }))

    const actionRows: FlatRow[] = fuzzyFilter(
      query,
      actions,
      (a) => a.label,
    ).map(({ item: a }) => ({
      group: 'actions',
      key: `action:${a.action.kind}`,
      label: a.label,
      hint: a.shortcut,
      selection: { kind: 'action', action: a.action },
    }))

    const slashRows: FlatRow[] = fuzzyFilter(
      query,
      SLASH_COMMANDS,
      (s) => `${s.command} ${s.description}`,
    ).map(({ item: s }) => ({
      group: 'slash',
      key: `slash:${s.command}`,
      label: s.command,
      hint: s.description,
      selection: { kind: 'slash', command: s.command },
    }))

    return [...sessionRows, ...actionRows, ...slashRows]
  }, [actions, conversations, query])

  useEffect(() => {
    if (!open) return
    setQuery('')
    setActiveIndex(0)
    requestAnimationFrame(() => inputRef.current?.focus())
  }, [open])

  useEffect(() => {
    setActiveIndex(0)
  }, [query])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
      } else if (event.key === 'ArrowDown') {
        event.preventDefault()
        setActiveIndex((i) => (rows.length === 0 ? 0 : (i + 1) % rows.length))
      } else if (event.key === 'ArrowUp') {
        event.preventDefault()
        setActiveIndex((i) => (rows.length === 0 ? 0 : (i - 1 + rows.length) % rows.length))
      } else if (event.key === 'Enter') {
        event.preventDefault()
        const row = rows[activeIndex]
        if (row) {
          onSelect(row.selection)
          onClose()
        }
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [activeIndex, onClose, onSelect, open, rows])

  useEffect(() => {
    if (!open) return
    const el = listRef.current?.querySelector(
      `[data-index="${activeIndex}"]`,
    ) as HTMLElement | null
    el?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, open])

  if (!open) return null

  const groups: Array<{ id: FlatRow['group']; label: string }> = [
    { id: 'sessions', label: 'Sessions' },
    { id: 'actions', label: 'Actions' },
    { id: 'slash', label: 'Slash commands' },
  ]

  return (
    <div
      className="fixed inset-0 z-40 flex items-start justify-center pt-[10vh] px-4 bg-black/40 animate-in"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="w-full max-w-xl rounded-lg bg-[var(--c-bg)] border border-[var(--c-border)] shadow-2xl overflow-hidden">
        <div className="border-b border-[var(--c-border)] px-3 py-2">
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search sessions, actions, slash commands…"
            className="w-full bg-transparent text-sm text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)]/60
                       focus:outline-none"
          />
        </div>

        <div ref={listRef} className="max-h-[60vh] overflow-y-auto py-1">
          {rows.length === 0 && (
            <div className="px-4 py-8 text-center text-sm text-[var(--c-text-secondary)]">
              No matches
            </div>
          )}

          {groups.map((g) => {
            const groupRows = rows.filter((r) => r.group === g.id)
            if (groupRows.length === 0) return null
            return (
              <div key={g.id} className="py-1">
                <div className="px-3 py-1 text-[10px] uppercase tracking-wider font-semibold text-[var(--c-text-secondary)]/60">
                  {g.label}
                </div>
                {groupRows.map((row) => {
                  const flatIdx = rows.indexOf(row)
                  const isActive = flatIdx === activeIndex
                  return (
                    <button
                      key={row.key}
                      type="button"
                      data-index={flatIdx}
                      onMouseEnter={() => setActiveIndex(flatIdx)}
                      onClick={() => {
                        onSelect(row.selection)
                        onClose()
                      }}
                      className={clsx(
                        'w-full flex items-center gap-2 px-3 py-2 text-left text-sm cursor-pointer transition-colors',
                        isActive
                          ? 'bg-[var(--c-accent)]/10 text-[var(--c-accent)]'
                          : 'text-[var(--c-text)]',
                      )}
                    >
                      <RowIcon group={row.group} theme={theme} />
                      <span className="flex-1 truncate">{row.label}</span>
                      {row.hint && (
                        <span className="text-[10px] text-[var(--c-text-secondary)]/70 font-mono uppercase">
                          {row.hint}
                        </span>
                      )}
                    </button>
                  )
                })}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function RowIcon({
  group,
  theme,
}: {
  group: FlatRow['group']
  theme: 'dark' | 'light'
}) {
  void theme
  if (group === 'sessions') {
    return <MessageSquare size={14} className="text-[var(--c-text-secondary)] shrink-0" />
  }
  if (group === 'slash') {
    return <Slash size={14} className="text-[var(--c-text-secondary)] shrink-0" />
  }
  return <Zap size={14} className="text-[var(--c-text-secondary)] shrink-0" />
}
