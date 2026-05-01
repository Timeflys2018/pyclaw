import { useState } from 'react'
import { ChevronDown, Wrench, Loader2, CheckCircle2, XCircle } from 'lucide-react'
import clsx from 'clsx'
import type { ToolCallInfo } from '../types'

interface Props {
  tool: ToolCallInfo
}

export default function ToolCallCard({ tool }: Props) {
  const [expanded, setExpanded] = useState(false)

  const statusIcon = {
    running: <Loader2 size={13} className="animate-spin text-[var(--c-accent)]" />,
    done: <CheckCircle2 size={13} className="text-[var(--c-success)]" />,
    error: <XCircle size={13} className="text-[var(--c-error)]" />,
  }[tool.status]

  return (
    <div className="mt-2 border border-[var(--c-border)] rounded-lg overflow-hidden bg-[var(--c-bg)]/50">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs cursor-pointer hover:bg-[var(--c-surface)]/50 transition-colors"
      >
        <Wrench size={12} className="text-[var(--c-text-secondary)] shrink-0" />
        <span className="font-mono font-medium text-[var(--c-text)]">{tool.name}</span>
        {statusIcon}
        <ChevronDown
          size={12}
          className={clsx(
            'ml-auto text-[var(--c-text-secondary)] transition-transform',
            expanded && 'rotate-180'
          )}
        />
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t border-[var(--c-border)]">
          <div className="pt-2">
            <span className="text-[10px] uppercase tracking-wider text-[var(--c-text-secondary)] font-semibold">
              Arguments
            </span>
            <pre className="mt-1 p-2 rounded bg-[var(--c-code-bg)] text-[10px] font-mono leading-relaxed overflow-x-auto text-[var(--c-text)]">
              {JSON.stringify(tool.args, null, 2)}
            </pre>
          </div>

          {tool.result && (
            <div>
              <span className="text-[10px] uppercase tracking-wider text-[var(--c-text-secondary)] font-semibold">
                Result
              </span>
              <pre className="mt-1 p-2 rounded bg-[var(--c-code-bg)] text-[10px] font-mono leading-relaxed overflow-x-auto max-h-48 text-[var(--c-text)]">
                {tool.result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
