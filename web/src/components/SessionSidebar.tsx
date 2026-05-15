import { useEffect, useMemo, useRef, useState } from 'react'
import { Plus, MessageSquare, PanelLeftClose, Pencil, Trash2 } from 'lucide-react'
import clsx from 'clsx'
import type { Conversation } from '../types'
import { SidebarRowSkeleton } from './Skeleton'

interface Props {
  conversations: Conversation[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onRename: (id: string, title: string) => Promise<void> | void
  onDeleteRequest: (id: string) => void
  collapsed: boolean
  onToggle: () => void
  isCreatingSession?: boolean
}

type TimeGroup = 'Today' | 'Last 7 days' | 'Earlier'

function getTimeGroup(ts: number): TimeGroup {
  const startOfToday = new Date()
  startOfToday.setHours(0, 0, 0, 0)
  const todayMs = startOfToday.getTime()
  const sevenDaysAgo = todayMs - 6 * 24 * 60 * 60 * 1000

  if (ts >= todayMs) return 'Today'
  if (ts >= sevenDaysAgo) return 'Last 7 days'
  return 'Earlier'
}

function groupAndSort(conversations: Conversation[]): { group: TimeGroup; items: Conversation[] }[] {
  const groups: Record<TimeGroup, Conversation[]> = {
    Today: [],
    'Last 7 days': [],
    Earlier: [],
  }

  for (const c of conversations) {
    const group = getTimeGroup(c.updatedAt)
    groups[group].push(c)
  }

  for (const key of Object.keys(groups) as TimeGroup[]) {
    groups[key].sort((a, b) => b.updatedAt - a.updatedAt)
  }

  const order: TimeGroup[] = ['Today', 'Last 7 days', 'Earlier']
  return order.filter((g) => groups[g].length > 0).map((g) => ({ group: g, items: groups[g] }))
}

interface RowProps {
  conv: Conversation
  active: boolean
  collapsed: boolean
  onSelect: () => void
  onRename: (title: string) => Promise<void> | void
  onDeleteRequest: () => void
}

function ConversationRow({ conv, active, collapsed, onSelect, onRename, onDeleteRequest }: RowProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(conv.title || 'New chat')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!editing) return
    setDraft(conv.title || 'New chat')
    requestAnimationFrame(() => {
      const el = inputRef.current
      if (!el) return
      el.focus()
      el.select()
    })
  }, [editing, conv.title])

  const commit = () => {
    const trimmed = draft.trim()
    setEditing(false)
    if (trimmed && trimmed !== conv.title) {
      void onRename(trimmed)
    }
  }

  const cancel = () => {
    setDraft(conv.title || 'New chat')
    setEditing(false)
  }

  if (editing) {
    return (
      <div
        className={clsx(
          'flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-sm',
          active
            ? 'bg-[var(--c-accent)]/10 text-[var(--c-accent)]'
            : 'text-[var(--c-text)] bg-[var(--c-surface)]',
        )}
      >
        <MessageSquare size={14} className="shrink-0" />
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              commit()
            } else if (e.key === 'Escape') {
              e.preventDefault()
              cancel()
            }
          }}
          onBlur={commit}
          maxLength={200}
          className="flex-1 min-w-0 bg-transparent border-0 focus:outline-none focus:ring-0 text-sm p-0 leading-tight"
        />
      </div>
    )
  }

  return (
    <div
      className={clsx(
        'group relative w-full rounded-lg transition-colors cursor-pointer',
        active
          ? 'bg-[var(--c-accent)]/10 text-[var(--c-accent)]'
          : 'text-[var(--c-text-secondary)] hover:bg-[var(--c-surface)] hover:text-[var(--c-text)]',
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        className="w-full flex items-center gap-2.5 px-2.5 py-2 text-left text-sm cursor-pointer"
      >
        <MessageSquare size={14} className="shrink-0" />
        {!collapsed && (
          <span className="flex-1 min-w-0 truncate pr-12">{conv.title || 'New chat'}</span>
        )}
      </button>

      {!collapsed && (
        <div className="absolute right-1 top-1/2 -translate-y-1/2 flex items-center gap-0.5
                        opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              setEditing(true)
            }}
            className="p-1 rounded hover:bg-[var(--c-bg)] text-[var(--c-text-secondary)] hover:text-[var(--c-text)] cursor-pointer"
            title="Rename"
          >
            <Pencil size={12} />
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onDeleteRequest()
            }}
            className="p-1 rounded hover:bg-[var(--c-error)]/10 text-[var(--c-text-secondary)] hover:text-[var(--c-error)] cursor-pointer"
            title="Delete"
          >
            <Trash2 size={12} />
          </button>
        </div>
      )}
    </div>
  )
}

export default function SessionSidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onRename,
  onDeleteRequest,
  collapsed,
  onToggle,
  isCreatingSession,
}: Props) {
  const grouped = useMemo(() => groupAndSort(conversations), [conversations])

  return (
    <aside
      className={clsx(
        'flex flex-col bg-[var(--c-sidebar)] border-r border-[var(--c-border)] transition-all duration-200',
        collapsed ? 'w-0 overflow-hidden md:w-14' : 'w-64',
      )}
    >
      <div className="flex items-center justify-between h-13 px-3 border-b border-[var(--c-border)] shrink-0">
        {!collapsed && (
          <span className="text-xs font-semibold uppercase tracking-wider text-[var(--c-text-secondary)]">
            Sessions
          </span>
        )}
        <button
          onClick={onToggle}
          className="p-1.5 rounded-md hover:bg-[var(--c-surface)] text-[var(--c-text-secondary)] cursor-pointer"
        >
          <PanelLeftClose size={16} className={clsx(collapsed && 'rotate-180')} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto py-2 px-2">
        {isCreatingSession && !collapsed && <SidebarRowSkeleton />}
        {grouped.map(({ group, items }) => (
          <div key={group} className="mb-3">
            {!collapsed && (
              <div className="px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--c-text-secondary)]/60">
                {group}
              </div>
            )}
            <div className="space-y-0.5">
              {items.map((c) => (
                <ConversationRow
                  key={c.id}
                  conv={c}
                  active={c.id === activeId}
                  collapsed={collapsed}
                  onSelect={() => onSelect(c.id)}
                  onRename={(title) => onRename(c.id, title)}
                  onDeleteRequest={() => onDeleteRequest(c.id)}
                />
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="p-2 border-t border-[var(--c-border)] shrink-0">
        <button
          onClick={onNew}
          className={clsx(
            'flex items-center gap-2 rounded-lg text-sm font-medium transition-colors cursor-pointer',
            'text-[var(--c-text-secondary)] hover:text-[var(--c-text)] hover:bg-[var(--c-surface)]',
            collapsed ? 'p-2 justify-center w-full' : 'px-2.5 py-2 w-full',
          )}
        >
          <Plus size={16} />
          {!collapsed && <span>New session</span>}
        </button>
      </div>
    </aside>
  )
}
