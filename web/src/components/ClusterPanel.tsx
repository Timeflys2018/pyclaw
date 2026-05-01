import clsx from 'clsx'
import { Server } from 'lucide-react'
import type { WorkerStatus } from '../types'

interface Props {
  workers: WorkerStatus[]
}

const statusColor: Record<string, string> = {
  healthy: 'bg-[var(--c-success)]',
  stale: 'bg-[var(--c-warning)]',
  dead: 'bg-[var(--c-error)]',
}

export default function ClusterPanel({ workers }: Props) {
  if (workers.length === 0) return null

  return (
    <div className="flex items-center gap-3 px-4 py-1.5 bg-[var(--c-sidebar)] border-b border-[var(--c-border)] text-xs">
      <Server size={12} className="text-[var(--c-text-secondary)]" />
      <span className="text-[var(--c-text-secondary)] font-medium">Cluster</span>
      <div className="flex items-center gap-1.5">
        {workers.map((w) => (
          <div
            key={w.worker_id}
            title={`${w.worker_id} — ${w.status}`}
            className={clsx('w-2 h-2 rounded-full', statusColor[w.status] ?? 'bg-gray-500')}
          />
        ))}
      </div>
      <span className="text-[var(--c-text-secondary)]/60 ml-auto tabular-nums">
        {workers.filter((w) => w.status === 'healthy').length}/{workers.length} healthy
      </span>
    </div>
  )
}
