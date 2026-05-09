import { useRef, useEffect, useState, type FormEvent } from 'react'
import { Send, Square, Clock } from 'lucide-react'
import type { Message } from '../types'
import { isProtocolOp } from '../protocol'
import MessageBubble from './MessageBubble'

interface Props {
  messages: Message[]
  streamingText: string
  isStreaming: boolean
  isQueued: boolean
  queuePosition: number
  onSend: (text: string) => void
  onAbort: () => void
}

export default function ChatArea({
  messages,
  streamingText,
  isStreaming,
  isQueued,
  queuePosition,
  onSend,
  onAbort,
}: Props) {
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingText])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const trimmed = input.trim()
    if (!trimmed) return
    if (isStreaming && !isProtocolOp(trimmed)) return
    onSend(trimmed)
    setInput('')
  }

  const streamingMessage: Message | null = streamingText
    ? { id: '__streaming__', role: 'assistant', content: streamingText, timestamp: Date.now() }
    : null

  return (
    <div className="flex flex-col flex-1 min-w-0">
      <div className="flex-1 overflow-y-auto px-4 py-6 md:px-8">
        <div className="max-w-3xl mx-auto w-full">
          {messages.length === 0 && !streamingMessage && (
            <div className="h-full flex flex-col items-center justify-center text-center min-h-[60vh]">
              <div className="p-4 rounded-2xl bg-[var(--c-surface)] border border-[var(--c-border)] mb-4">
                <span className="text-3xl">🐾</span>
              </div>
              <h2 className="text-lg font-display font-semibold text-[var(--c-text)] mb-1">
                What can I help with?
              </h2>
              <p className="text-sm text-[var(--c-text-secondary)] max-w-xs">
                Send a message to start a conversation with PyClaw.
              </p>
            </div>
          )}

          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}

          {streamingMessage && (
            <MessageBubble message={streamingMessage} isStreaming />
          )}

          {isQueued && (
            <div className="flex items-center gap-2 text-sm text-[var(--c-text-secondary)] py-3">
              <Clock size={14} className="animate-pulse" />
              <span>
                Queued{queuePosition > 0 ? ` (position ${queuePosition})` : ''} — waiting for current task…
              </span>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      <div className="shrink-0 border-t border-[var(--c-border)] px-4 py-3 md:px-8 bg-[var(--c-bg)]">
        <div className="max-w-3xl mx-auto w-full">
          {isStreaming && (
            <button
              onClick={onAbort}
              className="flex items-center gap-1.5 text-xs text-[var(--c-error)] hover:text-[var(--c-error)]/80
                         mb-2 cursor-pointer transition-colors"
            >
              <Square size={12} fill="currentColor" />
              Stop generating
            </button>
          )}

          <form onSubmit={submit} className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  submit(e)
                }
              }}
              placeholder="Send a message…"
              rows={1}
              className="flex-1 min-h-[44px] max-h-40 px-4 py-2.5 rounded-xl bg-[var(--c-surface)] border border-[var(--c-border)]
                         text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)]/50 text-sm resize-none
                         focus:outline-none focus:ring-2 focus:ring-[var(--c-accent)]/40 focus:border-[var(--c-accent)]
                         transition-all leading-relaxed"
            />
            <button
              type="submit"
              disabled={!input.trim() || (isStreaming && !isProtocolOp(input))}
              className="shrink-0 w-11 h-11 rounded-xl bg-[var(--c-accent)] text-white grid place-items-center
                         hover:brightness-110 active:scale-95
                         disabled:opacity-30 disabled:pointer-events-none
                         transition-all cursor-pointer"
            >
              <Send size={16} />
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
