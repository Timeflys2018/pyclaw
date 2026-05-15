import { AlertTriangle, X } from 'lucide-react'

interface Props {
  visible: boolean
  onReconnect: () => void
  onDismiss?: () => void
}

export default function ErrorBanner({ visible, onReconnect, onDismiss }: Props) {
  if (!visible) return null
  return (
    <div className="absolute top-0 left-0 right-0 z-30 mx-auto max-w-3xl mt-3 px-4">
      <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[var(--c-error)]/40
                      bg-[var(--c-error)]/10 text-sm text-[var(--c-text)] shadow-md">
        <AlertTriangle size={14} className="text-[var(--c-error)] shrink-0" />
        <span className="flex-1">
          Connection lost. Auto-reconnect failed multiple times.
        </span>
        <button
          type="button"
          onClick={onReconnect}
          className="text-xs font-medium px-2 py-1 rounded bg-[var(--c-error)]/20 hover:bg-[var(--c-error)]/30
                     transition-colors cursor-pointer"
        >
          Reconnect
        </button>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="p-1 rounded text-[var(--c-text-secondary)] hover:text-[var(--c-text)] cursor-pointer"
            title="Dismiss"
          >
            <X size={12} />
          </button>
        )}
      </div>
    </div>
  )
}
