import { useEffect } from 'react'
import { AlertTriangle } from 'lucide-react'

interface Props {
  open: boolean
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  destructive?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export default function ConfirmModal({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  destructive = false,
  onConfirm,
  onCancel,
}: Props) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onCancel()
      } else if (e.key === 'Enter') {
        e.preventDefault()
        onConfirm()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel, onConfirm])

  if (!open) return null

  const confirmClass = destructive
    ? 'bg-[var(--c-error)] text-white hover:brightness-110'
    : 'bg-[var(--c-accent)] text-white hover:brightness-110'

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4 bg-black/40 animate-in"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel()
      }}
    >
      <div className="w-full max-w-sm rounded-lg bg-[var(--c-bg)] border border-[var(--c-border)] shadow-2xl overflow-hidden">
        <div className="px-4 py-3 flex items-start gap-3">
          {destructive && (
            <AlertTriangle size={18} className="text-[var(--c-error)] shrink-0 mt-0.5" />
          )}
          <div className="flex-1 min-w-0">
            <div className="text-sm font-display font-semibold text-[var(--c-text)] mb-1">
              {title}
            </div>
            <p className="text-sm text-[var(--c-text-secondary)] break-words">{message}</p>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 px-3 pb-3">
          <button
            type="button"
            onClick={onCancel}
            className="px-3 py-1.5 rounded-md text-sm text-[var(--c-text-secondary)]
                       hover:text-[var(--c-text)] hover:bg-[var(--c-surface)] transition-colors cursor-pointer"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-all cursor-pointer
                       active:scale-[0.98] shadow-sm hover:shadow-md ${confirmClass}`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
