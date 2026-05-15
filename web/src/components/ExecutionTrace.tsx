import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import clsx from 'clsx'
import type { MessageMetadata, ToolCallInfo } from '../types'

interface Props {
  toolCalls?: ToolCallInfo[]
  metadata?: MessageMetadata
  isStreaming?: boolean
}

const MEMORY_HITS_FOLLOWUP_NOTE =
  'Memory hits (L1/L2/L3/L4) are not yet exposed by the WS protocol. Tracking as a follow-up.'

export default function ExecutionTrace({ toolCalls, metadata, isStreaming }: Props) {
  const [expanded, setExpanded] = useState<boolean>(Boolean(isStreaming))
  const [openToolIds, setOpenToolIds] = useState<Set<string>>(new Set())

  const hasTools = (toolCalls?.length ?? 0) > 0
  const hasMetadata = Boolean(
    metadata?.durationMs !== undefined ||
      metadata?.usage ||
      metadata?.model,
  )

  if (!hasTools && !hasMetadata && !isStreaming) {
    return null
  }

  const toolCount = toolCalls?.length ?? 0
  const summaryParts: string[] = []
  if (toolCount > 0) summaryParts.push(`${toolCount} tool${toolCount === 1 ? '' : 's'}`)
  summaryParts.push('mem n/a')
  if (metadata?.durationMs !== undefined) {
    summaryParts.push(formatDuration(metadata.durationMs))
  }
  if (metadata?.usage?.output !== undefined) {
    summaryParts.push(`${metadata.usage.output} tok`)
  }
  const summary = `Trace · ${summaryParts.join(' · ')}`

  const toggleTool = (id: string) => {
    setOpenToolIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div className="my-2 border border-[var(--c-border)] rounded-md bg-[var(--c-surface)]/40 text-xs font-mono">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-[var(--c-surface)]/60 transition-colors text-left"
      >
        <ChevronRight
          size={12}
          className={clsx(
            'text-[var(--c-text-secondary)] transition-transform shrink-0',
            expanded && 'rotate-90',
          )}
        />
        <span className="text-[var(--c-text-secondary)] truncate">
          {summary}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-[var(--c-border)] divide-y divide-[var(--c-border)]/60">
          {hasTools && (
            <div className="px-3 py-2 space-y-1">
              {toolCalls!.map((tc) => (
                <ToolTraceRow
                  key={tc.id}
                  tool={tc}
                  open={openToolIds.has(tc.id)}
                  onToggle={() => toggleTool(tc.id)}
                />
              ))}
            </div>
          )}

          <div className="px-3 py-2 text-[var(--c-text-secondary)]">
            <span className="text-[var(--c-text)]">Mem</span>:{' '}
            <span title={MEMORY_HITS_FOLLOWUP_NOTE}>protocol pending</span>
          </div>

          {hasMetadata && (
            <div className="px-3 py-2 text-[var(--c-text-secondary)] flex flex-wrap gap-x-3 gap-y-1">
              {metadata?.durationMs !== undefined && (
                <span>{formatDuration(metadata.durationMs)}</span>
              )}
              {metadata?.usage && (
                <span>
                  {metadata.usage.input ?? 0} in / {metadata.usage.output ?? 0} out
                  {metadata.usage.cacheRead !== undefined && metadata.usage.cacheRead > 0
                    ? ` · ${metadata.usage.cacheRead} cached`
                    : ''}
                </span>
              )}
              {metadata?.model && <span>model: {metadata.model}</span>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

interface ToolRowProps {
  tool: ToolCallInfo
  open: boolean
  onToggle: () => void
}

function ToolTraceRow({ tool, open, onToggle }: ToolRowProps) {
  const startGlyph = '▸'
  const endGlyph = '◂'
  const endStatusLabel =
    tool.status === 'error' ? 'err' : tool.status === 'done' ? 'ok' : 'running'
  const endColor =
    tool.status === 'error'
      ? 'text-[var(--c-error)]'
      : tool.status === 'done'
        ? 'text-[var(--c-success)]'
        : 'text-[var(--c-text-secondary)]'

  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center gap-2 cursor-pointer hover:bg-[var(--c-bg)]/40 rounded px-1 py-0.5 text-left"
      >
        <span className="text-[var(--c-accent)] shrink-0">{startGlyph}</span>
        <span className="text-[var(--c-text)] truncate">tool_call: {tool.name}</span>
        <span className={clsx('ml-auto shrink-0', endColor)}>
          {endGlyph} {endStatusLabel}
        </span>
      </button>
      {open && (
        <div className="pl-4 pr-1 py-1 space-y-1.5">
          <div>
            <span className="text-[10px] uppercase tracking-wider text-[var(--c-text-secondary)]">
              args
            </span>
            <pre className="mt-0.5 p-1.5 rounded bg-[var(--c-code-bg)] text-[10px] leading-snug overflow-x-auto text-[var(--c-text)]">
              {safeStringify(tool.args)}
            </pre>
          </div>
          {tool.result !== undefined && tool.result !== '' && (
            <div>
              <span className="text-[10px] uppercase tracking-wider text-[var(--c-text-secondary)]">
                result
              </span>
              <pre className="mt-0.5 p-1.5 rounded bg-[var(--c-code-bg)] text-[10px] leading-snug overflow-x-auto max-h-48 text-[var(--c-text)]">
                {tool.result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function safeStringify(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
