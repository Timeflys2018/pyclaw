import { Plus, MessageSquare, PanelLeftClose } from 'lucide-react'
import clsx from 'clsx'
import type { Conversation } from '../types'

interface Props {
  conversations: Conversation[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  collapsed: boolean
  onToggle: () => void
}

function timeAgo(ts: number): string {
  const diff = Date.now() - ts
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return 'now'
  if (mins < 60) return `${mins}m`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h`
  return `${Math.floor(hrs / 24)}d`
}

export default function SessionSidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  collapsed,
  onToggle,
}: Props) {
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

      <div className="flex-1 overflow-y-auto py-2 px-2 space-y-0.5">
        {conversations.map((c) => (
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
              <>
                <span className="flex-1 truncate">
                  {c.title || 'New chat'}
                </span>
                <span className="text-[10px] opacity-50 tabular-nums">
                  {timeAgo(c.updatedAt)}
                </span>
              </>
            )}
          </button>
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
