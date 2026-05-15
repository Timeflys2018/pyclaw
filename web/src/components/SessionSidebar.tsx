import { useMemo } from 'react'
import { Plus, MessageSquare, PanelLeftClose } from 'lucide-react'
import clsx from 'clsx'
import type { Conversation } from '../types'
import { SidebarRowSkeleton } from './Skeleton'

interface Props {
  conversations: Conversation[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
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
    'Today': [],
    'Last 7 days': [],
    'Earlier': [],
  }

  for (const c of conversations) {
    const group = getTimeGroup(c.updatedAt)
    groups[group].push(c)
  }

  for (const key of Object.keys(groups) as TimeGroup[]) {
    groups[key].sort((a, b) => b.updatedAt - a.updatedAt)
  }

  const order: TimeGroup[] = ['Today', 'Last 7 days', 'Earlier']
  return order
    .filter((g) => groups[g].length > 0)
    .map((g) => ({ group: g, items: groups[g] }))
}

export default function SessionSidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  collapsed,
  onToggle,
  isCreatingSession,
}: Props) {
  const grouped = useMemo(() => groupAndSort(conversations), [conversations])

  return (
    <aside
      className={clsx(
        'flex flex-col bg-[var(--c-sidebar)] border-r border-[var(--c-border)] transition-all duration-200',
        collapsed ? 'w-0 overflow-hidden md:w-14' : 'w-64'
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
                <button
                  key={c.id}
                  onClick={() => onSelect(c.id)}
                  className={clsx(
                    'w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-left transition-colors text-sm cursor-pointer',
                    c.id === activeId
                      ? 'bg-[var(--c-accent)]/10 text-[var(--c-accent)]'
                      : 'text-[var(--c-text-secondary)] hover:bg-[var(--c-surface)] hover:text-[var(--c-text)]'
                  )}
                >
                  <MessageSquare size={14} className="shrink-0" />
                  {!collapsed && (
                    <span className="flex-1 truncate">
                      {c.title || 'New chat'}
                    </span>
                  )}
                </button>
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
            collapsed ? 'p-2 justify-center w-full' : 'px-2.5 py-2 w-full'
          )}
        >
          <Plus size={16} />
          {!collapsed && <span>New session</span>}
        </button>
      </div>
    </aside>
  )
}
