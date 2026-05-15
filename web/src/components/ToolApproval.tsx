import { ShieldAlert, Check, X } from 'lucide-react'
import type { PendingApproval } from '../types'
import { usePermissionStore } from '../stores'

interface Props {
  approval: PendingApproval
  onApprove: () => void
  onReject: () => void
}

export default function ToolApprovalModal({ approval, onApprove, onReject }: Props) {
  const tier = usePermissionStore((s) => s.tier)
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 backdrop-blur-sm p-4">
      <div
        className="w-full max-w-md bg-[var(--c-surface)] border border-[var(--c-border)] rounded-2xl shadow-2xl overflow-hidden animate-in"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 px-5 py-4 border-b border-[var(--c-border)] bg-[var(--c-warning)]/5">
          <ShieldAlert size={20} className="text-[var(--c-warning)] shrink-0" />
          <div className="flex-1">
            <div className="flex items-center justify-between gap-2">
              <h3 className="font-semibold text-sm text-[var(--c-text)]">
                Tool Approval Required
              </h3>
              <span
                data-testid="tier-badge"
                className="text-[10px] uppercase tracking-wider font-semibold px-2 py-0.5 rounded-full
                           bg-blue-500/15 text-blue-600 dark:text-blue-400"
              >
                🛡 {tier}
              </span>
            </div>
            <p className="text-xs text-[var(--c-text-secondary)] mt-0.5">
              {approval.reason}
            </p>
          </div>
        </div>

        <div className="px-5 py-4 space-y-3">
          <div>
            <span className="text-[10px] uppercase tracking-wider text-[var(--c-text-secondary)] font-semibold">
              Tool
            </span>
            <p className="font-mono text-sm font-medium text-[var(--c-text)] mt-0.5">
              {approval.toolName}
            </p>
          </div>

          <div>
            <span className="text-[10px] uppercase tracking-wider text-[var(--c-text-secondary)] font-semibold">
              Arguments
            </span>
            <pre className="mt-1 p-3 rounded-lg bg-[var(--c-code-bg)] text-xs font-mono leading-relaxed overflow-x-auto max-h-56 text-[var(--c-text)]">
              {JSON.stringify(approval.args, null, 2)}
            </pre>
          </div>
        </div>

        <div className="flex gap-3 px-5 py-4 border-t border-[var(--c-border)]">
          <button
            onClick={onReject}
            className="flex-1 h-10 rounded-lg border border-[var(--c-error)]/30 text-[var(--c-error)] text-sm font-medium
                       flex items-center justify-center gap-1.5
                       hover:bg-[var(--c-error)]/10 active:scale-[0.98] transition-all cursor-pointer"
          >
            <X size={15} />
            Reject
          </button>
          <button
            onClick={onApprove}
            className="flex-1 h-10 rounded-lg bg-[var(--c-success)] text-white text-sm font-medium
                       flex items-center justify-center gap-1.5
                       hover:brightness-110 active:scale-[0.98] transition-all cursor-pointer"
          >
            <Check size={15} />
            Approve
          </button>
        </div>
      </div>
    </div>
  )
}
