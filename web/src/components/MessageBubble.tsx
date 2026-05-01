import clsx from 'clsx'
import type { Message } from '../types'
import ToolCallCard from './ToolCall'

interface Props {
  message: Message
  isStreaming?: boolean
}

function renderContent(text: string) {
  const parts = text.split(/(```[\s\S]*?```)/g)
  return parts.map((part, i) => {
    if (part.startsWith('```') && part.endsWith('```')) {
      const inner = part.slice(3, -3)
      const newline = inner.indexOf('\n')
      const lang = newline > 0 ? inner.slice(0, newline).trim() : ''
      const code = newline > 0 ? inner.slice(newline + 1) : inner
      return (
        <pre
          key={i}
          className="my-2 p-3 rounded-lg bg-[var(--c-code-bg)] overflow-x-auto text-xs leading-relaxed"
        >
          {lang && (
            <span className="block text-[10px] text-[var(--c-text-secondary)] mb-1.5 uppercase tracking-wider font-semibold">
              {lang}
            </span>
          )}
          <code>{code}</code>
        </pre>
      )
    }

    const lines = part.split('\n')
    return lines.map((line, li) => {
      if (!line && li < lines.length - 1) return <br key={`${i}-${li}`} />

      const rendered = line.replace(
        /`([^`]+)`/g,
        '<code class="px-1 py-0.5 rounded bg-[var(--c-code-bg)] text-xs">$1</code>'
      )

      if (line.startsWith('**') && line.endsWith('**')) {
        return (
          <p
            key={`${i}-${li}`}
            className="font-semibold"
            dangerouslySetInnerHTML={{ __html: rendered.slice(2, -2) }}
          />
        )
      }

      return (
        <p
          key={`${i}-${li}`}
          dangerouslySetInnerHTML={{ __html: rendered }}
        />
      )
    })
  })
}

export default function MessageBubble({ message, isStreaming }: Props) {
  const isUser = message.role === 'user'

  return (
    <div
      className={clsx(
        'flex w-full mb-4',
        isUser ? 'justify-end' : 'justify-start'
      )}
    >
      <div
        className={clsx(
          'max-w-[75%] rounded-2xl px-4 py-3 text-sm leading-relaxed',
          isUser
            ? 'bg-[var(--c-accent)] text-white rounded-br-md'
            : 'bg-[var(--c-surface)] text-[var(--c-text)] border border-[var(--c-border)] rounded-bl-md'
        )}
      >
        <div className="space-y-1 break-words [&_code]:font-mono">
          {renderContent(message.content)}
        </div>

        {message.toolCalls?.map((tc) => (
          <ToolCallCard key={tc.id} tool={tc} />
        ))}

        {isStreaming && (
          <span className="inline-block w-1.5 h-4 bg-[var(--c-text)] opacity-70 animate-pulse ml-0.5 -mb-0.5 rounded-sm" />
        )}
      </div>
    </div>
  )
}
