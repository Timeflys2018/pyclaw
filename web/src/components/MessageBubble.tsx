import type { Message } from '../types'
import ToolCallCard from './ToolCall'
import MarkdownRenderer from './MarkdownRenderer'

interface Props {
  message: Message
  isStreaming?: boolean
}

function renderPlainText(text: string) {
  return text.split('\n').map((line, i) => (
    <span key={i}>
      {line}
      {i < text.split('\n').length - 1 && <br />}
    </span>
  ))
}

export default function MessageBubble({ message, isStreaming }: Props) {
  const isUser = message.role === 'user'

  return (
    <div className={`flex w-full mb-6 ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-[80%] ${isUser ? 'items-end' : 'items-start'}`}>
        <div className={`flex items-center gap-2 mb-1 ${isUser ? 'justify-end' : 'justify-start'}`}>
          <span className="text-xs font-medium text-[var(--c-text-secondary)] uppercase tracking-wide">
            {isUser ? 'You' : '🐾 PyClaw'}
          </span>
        </div>

        <div
          className={
            isUser
              ? 'rounded-2xl rounded-br-md px-4 py-3 bg-[var(--c-msg-user-bg)] border border-[var(--c-border)] text-sm leading-relaxed text-[var(--c-text)]'
              : 'text-sm leading-relaxed text-[var(--c-text)]'
          }
        >
          {isUser ? (
            <div className="whitespace-pre-wrap break-words">
              {renderPlainText(message.content)}
            </div>
          ) : (
            <MarkdownRenderer content={message.content} className="break-words [&_code]:font-mono" />
          )}

          {message.toolCalls?.map((tc) => (
            <ToolCallCard key={tc.id} tool={tc} />
          ))}

          {isStreaming && (
            <span className="inline-block w-1.5 h-4 bg-[var(--c-text)] opacity-70 animate-pulse ml-0.5 -mb-0.5 rounded-sm" />
          )}
        </div>
      </div>
    </div>
  )
}
