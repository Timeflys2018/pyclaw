import clsx from 'clsx'
import { Server } from 'lucide-react'
import type { WorkerStatus } from '../types'

interface Props {
  workers: WorkerStatus[]
  activeWorkerId?: string | null
}

const statusColor: Record<string, string> = {
  healthy: 'bg-[var(--c-success)]',
  stale: 'bg-[var(--c-warning)]',
  dead: 'bg-[var(--c-error)]',
}

export default function ClusterPanel({ workers, activeWorkerId }: Props) {
  if (workers.length === 0) return null

  return (
    <div className="flex items-center gap-3 px-4 h-8 bg-[var(--c-statusbar-bg)] border-t border-[var(--c-border)] text-xs shrink-0">
      <Server size={11} className="text-[var(--c-text-secondary)]" />
      <div className="flex items-center gap-1.5">
        {workers.map((w) => (
          <div
            key={w.worker_id}
            title={`${w.worker_id} — ${w.status}`}
            className={clsx(
              'w-2 h-2 rounded-full',
              statusColor[w.status] ?? 'bg-gray-500',
              w.worker_id === activeWorkerId && 'ring-2 ring-[var(--c-accent)] ring-offset-1 ring-offset-[var(--c-statusbar-bg)]'
            )}
          />
        ))}
      </div>
      <span className="text-[var(--c-text-secondary)]/60 ml-auto tabular-nums">
        {workers.filter((w) => w.status === 'healthy').length}/{workers.length} healthy
      </span>
    </div>
  )
}
