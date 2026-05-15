import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Send, Square, Clock, ArrowDown } from 'lucide-react'
import type { Message } from '../types'
import { isProtocolOp } from '../protocol'
import MessageBubble from './MessageBubble'
import EmptyStateSuggestions from './EmptyStateSuggestions'
import { MessageSkeleton } from './Skeleton'

interface Props {
  messages: Message[]
  streamingText: string
  isStreaming: boolean
  isQueued: boolean
  queuePosition: number
  isLoadingHistory?: boolean
  prefillInput?: { text: string; nonce: number } | null
  onSend: (text: string) => void
  onAbort: () => void
  onRetryMessage?: (messageId: string) => void
}

const STREAMING_ID = '__streaming__'
const STICK_TO_BOTTOM_THRESHOLD_PX = 80

export default function ChatArea({
  messages,
  streamingText,
  isStreaming,
  isQueued,
  queuePosition,
  isLoadingHistory,
  prefillInput,
  onSend,
  onAbort,
  onRetryMessage,
}: Props) {
  const [input, setInput] = useState('')
  const [stickToBottom, setStickToBottom] = useState(true)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const focusInputAtEnd = useCallback(() => {
    requestAnimationFrame(() => {
      const el = textareaRef.current
      if (el) {
        el.focus()
        const len = el.value.length
        el.setSelectionRange(len, len)
      }
    })
  }, [])

  const handlePickSuggestion = useCallback(
    (prompt: string) => {
      setInput(prompt)
      focusInputAtEnd()
    },
    [focusInputAtEnd],
  )

  useEffect(() => {
    if (!prefillInput) return
    setInput(prefillInput.text)
    focusInputAtEnd()
  }, [prefillInput, focusInputAtEnd])

  const scrollerRef = useRef<HTMLDivElement>(null)
  const scrollFrameRef = useRef<number | null>(null)
  const programmaticScrollUntilRef = useRef(0)

  const items = useMemo<Message[]>(() => {
    if (!streamingText) return messages
    const streaming: Message = {
      id: STREAMING_ID,
      role: 'assistant',
      content: streamingText,
      timestamp: Date.now(),
    }
    return [...messages, streaming]
  }, [messages, streamingText])

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollerRef.current,
    estimateSize: () => 96,
    overscan: 6,
    getItemKey: (index) => items[index]?.id ?? index,
  })

  const scrollToBottomNow = useCallback(() => {
    const el = scrollerRef.current
    if (!el) return
    // Programmatic scroll lands two render cycles later because the
    // virtualizer first paints with estimateSize and then re-measures via
    // measureElement. We mark a quiet window so handleScroll ignores the
    // intermediate onScroll events and does not flip stickToBottom off.
    programmaticScrollUntilRef.current = performance.now() + 350
    el.scrollTop = el.scrollHeight
    if (items.length > 0) {
      virtualizer.scrollToIndex(items.length - 1, { align: 'end' })
    }
  }, [items.length, virtualizer])

  useLayoutEffect(() => {
    if (!stickToBottom) return
    if (scrollFrameRef.current !== null) return
    scrollFrameRef.current = requestAnimationFrame(() => {
      scrollFrameRef.current = null
      scrollToBottomNow()
      // Schedule a second pass after the virtualizer's measureElement
      // pass settles real heights, so scrollHeight reflects actual content.
      requestAnimationFrame(() => scrollToBottomNow())
    })
    return () => {
      if (scrollFrameRef.current !== null) {
        cancelAnimationFrame(scrollFrameRef.current)
        scrollFrameRef.current = null
      }
    }
  }, [items.length, streamingText, stickToBottom, scrollToBottomNow])

  const handleScroll = useCallback(() => {
    if (performance.now() < programmaticScrollUntilRef.current) return
    const el = scrollerRef.current
    if (!el) return
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    setStickToBottom(distanceFromBottom <= STICK_TO_BOTTOM_THRESHOLD_PX)
  }, [])

  const submit = useCallback(() => {
    const trimmed = input.trim()
    if (!trimmed) return
    if (isStreaming && !isProtocolOp(trimmed)) return
    onSend(trimmed)
    setInput('')
    setStickToBottom(true)
    requestAnimationFrame(() => scrollToBottomNow())
  }, [input, isStreaming, onSend, scrollToBottomNow])

  const handleJumpToLatest = useCallback(() => {
    setStickToBottom(true)
    scrollToBottomNow()
  }, [scrollToBottomNow])

  const totalSize = virtualizer.getTotalSize()
  const virtualItems = virtualizer.getVirtualItems()
  const showEmptyState = items.length === 0 && !isLoadingHistory

  return (
    <div className="flex flex-col flex-1 min-w-0 relative">
      <div
        ref={scrollerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-4 py-6 md:px-8"
      >
        <div className="max-w-3xl mx-auto w-full">
          {isLoadingHistory && items.length === 0 && (
            <div className="pt-4">
              <MessageSkeleton />
            </div>
          )}

          {showEmptyState && (
            <EmptyStateSuggestions onPick={handlePickSuggestion} />
          )}

          {!showEmptyState && !isLoadingHistory && (
            <div style={{ height: totalSize, position: 'relative', width: '100%' }}>
              {virtualItems.map((vi) => {
                const item = items[vi.index]
                if (!item) return null
                const isStreamingItem = item.id === STREAMING_ID
                return (
                  <div
                    key={vi.key}
                    data-index={vi.index}
                    ref={virtualizer.measureElement}
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      transform: `translateY(${vi.start}px)`,
                    }}
                  >
                    <MessageBubble
                      message={item}
                      isStreaming={isStreamingItem}
                      onRetry={
                        item.role === 'error' && onRetryMessage
                          ? () => onRetryMessage(item.id)
                          : undefined
                      }
                    />
                  </div>
                )
              })}
            </div>
          )}

          {isQueued && (
            <div className="flex items-center gap-2 text-sm text-[var(--c-text-secondary)] py-3">
              <Clock size={14} className="animate-pulse" />
              <span>
                Queued{queuePosition > 0 ? ` (position ${queuePosition})` : ''} — waiting for current task…
              </span>
            </div>
          )}
        </div>
      </div>

      {!stickToBottom && items.length > 0 && (
        <button
          onClick={handleJumpToLatest}
          className="absolute right-6 bottom-28 flex items-center gap-1.5 px-3 py-1.5 rounded-full
                     bg-[var(--c-surface)] border border-[var(--c-border)] text-xs text-[var(--c-text)]
                     shadow-md hover:bg-[var(--c-bg)] transition-colors cursor-pointer"
        >
          <ArrowDown size={12} />
          Jump to latest
        </button>
      )}

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

          <form
            onSubmit={(e) => {
              e.preventDefault()
              submit()
            }}
            className="flex items-end gap-2"
          >
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  submit()
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
