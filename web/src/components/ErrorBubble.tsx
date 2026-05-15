import { AlertCircle, RotateCcw } from 'lucide-react'
import type { Message } from '../types'

interface Props {
  message: Message
  onRetry?: () => void
}

export default function ErrorBubble({ message, onRetry }: Props) {
  return (
    <div className="flex w-full mb-6 justify-start">
      <div className="max-w-[80%] flex items-start gap-2 px-4 py-3 rounded-2xl border border-[var(--c-error)]/40 bg-[var(--c-error)]/5 text-sm text-[var(--c-text)]">
        <AlertCircle size={16} className="text-[var(--c-error)] shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="text-[10px] uppercase tracking-wider text-[var(--c-error)] font-semibold mb-1">
            Error
          </div>
          <div className="break-words leading-relaxed">{message.content}</div>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-2 inline-flex items-center gap-1.5 text-xs text-[var(--c-text-secondary)] hover:text-[var(--c-text)]
                         transition-colors cursor-pointer"
            >
              <RotateCcw size={12} />
              Retry
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
