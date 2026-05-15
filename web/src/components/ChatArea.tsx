import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import {
  Send,
  Square,
  Clock,
  ArrowDown,
  Paperclip,
  X,
  Eye,
  Shield,
  Zap,
  ChevronDown,
} from 'lucide-react'
import type { ImageBlock, Message } from '../types'
import { isProtocolOp } from '../protocol'
import MessageBubble from './MessageBubble'
import EmptyStateSuggestions from './EmptyStateSuggestions'
import { MessageSkeleton } from './Skeleton'
import {
  fileToImageBlock,
  imageBlockToDataUrl,
  isImageError,
  MAX_IMAGES_PER_MESSAGE,
} from '../lib/image'
import { usePermissionStore, type PermissionTier } from '../stores'

const TIER_OPTIONS: ReadonlyArray<{
  tier: PermissionTier
  label: string
  Icon: typeof Eye
  triggerClass: string
}> = [
  {
    tier: 'read-only',
    label: 'read-only',
    Icon: Eye,
    triggerClass: 'text-gray-600 dark:text-gray-300',
  },
  {
    tier: 'approval',
    label: 'approval',
    Icon: Shield,
    triggerClass: 'text-blue-600 dark:text-blue-400',
  },
  {
    tier: 'yolo',
    label: 'yolo',
    Icon: Zap,
    triggerClass: 'text-orange-600 dark:text-orange-400',
  },
]

function PermissionTierDropdown() {
  const tier = usePermissionStore((s) => s.tier)
  const setTier = usePermissionStore((s) => s.setTier)
  const [open, setOpen] = useState(false)
  const wrapperRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onDocumentClick(event: MouseEvent) {
      const target = event.target
      if (!(target instanceof Node)) return
      if (wrapperRef.current && !wrapperRef.current.contains(target)) {
        setOpen(false)
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocumentClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocumentClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const current = TIER_OPTIONS.find((o) => o.tier === tier) ?? TIER_OPTIONS[1]
  const CurrentIcon = current.Icon

  return (
    <div
      ref={wrapperRef}
      className="relative"
      data-testid="permission-dropdown"
      data-tier={tier}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1.5 px-2 py-1 rounded-md border border-[var(--c-border)]
                    bg-[var(--c-surface)] hover:bg-[var(--c-bg)] text-[11px] cursor-pointer
                    transition-colors ${current.triggerClass}`}
        title={`Permission tier: ${current.label}`}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <CurrentIcon size={12} />
        <span className="font-medium">{current.label}</span>
        <ChevronDown size={11} className="opacity-60" />
      </button>

      {open && (
        <ul
          role="listbox"
          aria-label="Permission tier"
          className="absolute bottom-full left-0 mb-1 min-w-[160px] z-20
                     bg-[var(--c-surface)] border border-[var(--c-border)] rounded-md
                     shadow-lg overflow-hidden"
        >
          {TIER_OPTIONS.map(({ tier: t, label, Icon, triggerClass }) => {
            const selected = t === tier
            return (
              <li
                key={t}
                role="option"
                aria-selected={selected}
                data-testid={`tier-option-${t}`}
              >
                <button
                  type="button"
                  onClick={() => {
                    setTier(t)
                    setOpen(false)
                  }}
                  className={`w-full flex items-center gap-2 px-3 py-2 text-[12px]
                              hover:bg-[var(--c-bg)] cursor-pointer
                              ${selected ? 'bg-[var(--c-bg)] font-medium' : ''}
                              ${triggerClass}`}
                >
                  <Icon size={13} />
                  <span>{label}</span>
                  {selected && (
                    <span className="ml-auto text-[10px] opacity-60">●</span>
                  )}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

interface Props {
  messages: Message[]
  streamingText: string
  isStreaming: boolean
  isQueued: boolean
  queuePosition: number
  isLoadingHistory?: boolean
  prefillInput?: { text: string; nonce: number } | null
  onSend: (text: string, attachments?: ImageBlock[]) => void
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
  const [attachments, setAttachments] = useState<ImageBlock[]>([])
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [stickToBottom, setStickToBottom] = useState(true)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dragCounterRef = useRef(0)

  const addFiles = useCallback(async (files: File[]) => {
    const images = files.filter((f) => f.type.startsWith('image/'))
    if (images.length === 0) return false
    setAttachmentError(null)
    const results = await Promise.all(images.map(fileToImageBlock))
    let added = 0
    setAttachments((prev) => {
      const next = [...prev]
      for (const r of results) {
        if (isImageError(r)) {
          setAttachmentError(r.message)
          continue
        }
        if (next.length >= MAX_IMAGES_PER_MESSAGE) {
          setAttachmentError(
            `Capped at ${MAX_IMAGES_PER_MESSAGE} images per message; extras dropped.`,
          )
          break
        }
        next.push(r)
        added += 1
      }
      return next
    })
    return added > 0
  }, [])

  const removeAttachment = useCallback((index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index))
    setAttachmentError(null)
  }, [])

  const handlePaste = useCallback(
    (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
      const items = event.clipboardData?.items
      if (!items) return
      const files: File[] = []
      for (const item of items) {
        if (item.kind === 'file' && item.type.startsWith('image/')) {
          const f = item.getAsFile()
          if (f) files.push(f)
        }
      }
      if (files.length === 0) return
      event.preventDefault()
      void addFiles(files)
    },
    [addFiles],
  )

  const handleDrop = useCallback(
    (event: React.DragEvent<Element>) => {
      event.preventDefault()
      setIsDragging(false)
      dragCounterRef.current = 0
      const files = Array.from(event.dataTransfer?.files ?? []).filter((f) =>
        f.type.startsWith('image/'),
      )
      if (files.length === 0) return
      void addFiles(files)
    },
    [addFiles],
  )

  const handleDragEnter = useCallback((event: React.DragEvent<Element>) => {
    if (!Array.from(event.dataTransfer?.types ?? []).includes('Files')) return
    dragCounterRef.current += 1
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback(() => {
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1)
    if (dragCounterRef.current === 0) setIsDragging(false)
  }, [])

  const handleFilePicked = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? [])
      event.target.value = ''
      if (files.length === 0) return
      void addFiles(files)
    },
    [addFiles],
  )

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
    const hasAttachments = attachments.length > 0
    if (!trimmed && !hasAttachments) return
    if (isStreaming && !isProtocolOp(trimmed)) return
    onSend(trimmed, hasAttachments ? attachments : undefined)
    setInput('')
    setAttachments([])
    setAttachmentError(null)
    setStickToBottom(true)
    requestAnimationFrame(() => scrollToBottomNow())
  }, [attachments, input, isStreaming, onSend, scrollToBottomNow])

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

      <div className="shrink-0 px-4 py-3 md:px-8 bg-[var(--c-bg)]">
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
            onDragEnter={handleDragEnter}
            onDragLeave={handleDragLeave}
            onDragOver={(e) => {
              if (Array.from(e.dataTransfer?.types ?? []).includes('Files')) {
                e.preventDefault()
              }
            }}
            onDrop={handleDrop}
            className={`relative rounded-2xl border bg-[var(--c-surface)] shadow-sm
                       transition-all focus-within:shadow-md
                       ${
                         isDragging
                           ? 'border-[var(--c-accent)] ring-2 ring-[var(--c-accent)]/30'
                           : 'border-[var(--c-border)] focus-within:border-[var(--c-text-secondary)]/30'
                       }`}
          >
            {attachments.length > 0 && (
              <div className="flex flex-wrap gap-2 px-3 pt-3">
                {attachments.map((att, i) => (
                  <div
                    key={`${att.mime_type}-${i}`}
                    className="relative w-16 h-16 rounded-md overflow-hidden border border-[var(--c-border)] bg-[var(--c-bg)]"
                  >
                    <img
                      src={imageBlockToDataUrl(att)}
                      alt={`attachment ${i + 1}`}
                      className="w-full h-full object-cover"
                    />
                    <button
                      type="button"
                      onClick={() => removeAttachment(i)}
                      className="absolute top-0.5 right-0.5 w-4 h-4 rounded-full bg-black/60 text-white grid place-items-center
                                 hover:bg-black/80 transition-colors cursor-pointer"
                      title="Remove"
                    >
                      <X size={10} />
                    </button>
                  </div>
                ))}
              </div>
            )}

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
              onPaste={handlePaste}
              placeholder="Send a message…"
              rows={2}
              className="block w-full min-h-[60px] max-h-60 px-4 pt-3 pb-1 bg-transparent border-0
                         text-[var(--c-text)] placeholder:text-[var(--c-text-secondary)]/50 text-sm resize-none
                         focus:outline-none focus:ring-0 leading-relaxed"
            />

            <div className="flex items-center justify-between px-2 pb-2">
              <PermissionTierDropdown />

              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/gif,image/webp"
                multiple
                onChange={handleFilePicked}
                className="hidden"
              />

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={attachments.length >= MAX_IMAGES_PER_MESSAGE}
                  className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[var(--c-text-secondary)]
                             hover:text-[var(--c-text)] hover:bg-[var(--c-bg)]
                             disabled:opacity-30 disabled:pointer-events-none
                             transition-colors cursor-pointer"
                  title="Attach image"
                >
                  <Paperclip size={14} />
                </button>

                <button
                  type="submit"
                  disabled={
                    (!input.trim() && attachments.length === 0) ||
                    (isStreaming && !isProtocolOp(input))
                  }
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--c-accent)] text-white text-sm font-medium
                             hover:brightness-110 active:scale-[0.98] shadow-sm hover:shadow-md
                             disabled:opacity-40 disabled:pointer-events-none
                             transition-all cursor-pointer"
                >
                  <Send size={14} />
                  <span>Send</span>
                </button>
              </div>
            </div>

            {isDragging && (
              <div className="absolute inset-0 rounded-2xl bg-[var(--c-accent)]/10 grid place-items-center pointer-events-none">
                <span className="text-sm font-medium text-[var(--c-accent)]">
                  Drop images to attach
                </span>
              </div>
            )}
          </form>

          {attachmentError ? (
            <p className="mt-1.5 text-[11px] text-[var(--c-error)] text-center">
              {attachmentError}
            </p>
          ) : (
            <p className="mt-1.5 text-[11px] text-[var(--c-text-secondary)]/60 text-center">
              Press Enter to send · Shift + Enter for newline · Paste or drop images to attach
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
