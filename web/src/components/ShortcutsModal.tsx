import { useEffect } from 'react'
import { X } from 'lucide-react'
import { modKeyLabel } from '../hooks/useGlobalKeyboard'

interface Props {
  open: boolean
  onClose: () => void
}

interface Entry {
  keys: string[]
  description: string
}

export default function ShortcutsModal({ open, onClose }: Props) {
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, open])

  if (!open) return null

  const mod = modKeyLabel()
  const entries: Entry[] = [
    { keys: [mod, 'K'], description: 'Open command palette' },
    { keys: [mod, 'N'], description: 'New session' },
    { keys: [mod, '\\'], description: 'Toggle sidebar' },
    { keys: [mod, '/'], description: 'Show this help' },
    { keys: [mod, 'Enter'], description: 'Force-send (interrupts streaming)' },
    { keys: ['Esc'], description: 'Close any open modal or palette' },
    { keys: ['Enter'], description: 'Send the current message (in chat input)' },
    { keys: ['Shift', 'Enter'], description: 'Newline in the chat input' },
  ]

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4 bg-black/40 animate-in"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="w-full max-w-md rounded-lg bg-[var(--c-bg)] border border-[var(--c-border)] shadow-2xl overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--c-border)]">
          <span className="text-sm font-display font-semibold text-[var(--c-text)]">
            Keyboard shortcuts
          </span>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded text-[var(--c-text-secondary)] hover:text-[var(--c-text)] hover:bg-[var(--c-surface)] cursor-pointer"
          >
            <X size={14} />
          </button>
        </div>
        <ul className="px-4 py-3 space-y-2 text-sm">
          {entries.map((entry, i) => (
            <li key={i} className="flex items-center justify-between gap-3">
              <span className="text-[var(--c-text-secondary)]">{entry.description}</span>
              <span className="flex items-center gap-1">
                {entry.keys.map((k) => (
                  <kbd
                    key={k}
                    className="px-1.5 py-0.5 text-[10px] rounded border border-[var(--c-border)]
                               bg-[var(--c-surface)] text-[var(--c-text)] font-mono"
                  >
                    {k}
                  </kbd>
                ))}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
