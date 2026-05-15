import type { ContentBlock, ImageBlock, Message } from '../types'
import MarkdownRenderer from './MarkdownRenderer'
import ExecutionTrace from './ExecutionTrace'
import SystemMessage from './SystemMessage'
import ErrorBubble from './ErrorBubble'
import { imageBlockToDataUrl } from '../lib/image'

interface Props {
  message: Message
  isStreaming?: boolean
  onRetry?: () => void
}

function renderPlainText(text: string) {
  const lines = text.split('\n')
  return lines.map((line, i) => (
    <span key={i}>
      {line}
      {i < lines.length - 1 && <br />}
    </span>
  ))
}

interface SplitContent {
  text: string
  images: ImageBlock[]
}

function splitMessageContent(content: string | ContentBlock[]): SplitContent {
  if (typeof content === 'string') {
    return { text: content, images: [] }
  }
  let text = ''
  const images: ImageBlock[] = []
  for (const block of content) {
    if (block.type === 'text') {
      text += (text ? '\n' : '') + block.text
    } else if (block.type === 'image') {
      images.push(block)
    }
  }
  return { text, images }
}

function ImageGrid({ images, alignRight }: { images: ImageBlock[]; alignRight: boolean }) {
  return (
    <div className={`flex flex-wrap gap-2 mb-2 ${alignRight ? 'justify-end' : 'justify-start'}`}>
      {images.map((img, i) => {
        const url = imageBlockToDataUrl(img)
        return (
          <a
            key={`${img.mime_type}-${i}`}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="block max-w-[280px] rounded-md overflow-hidden border border-[var(--c-border)] bg-[var(--c-bg)]
                       hover:opacity-90 transition-opacity"
          >
            <img src={url} alt={`image ${i + 1}`} className="block max-h-60 object-contain" />
          </a>
        )
      })}
    </div>
  )
}

export default function MessageBubble({ message, isStreaming, onRetry }: Props) {
  if (message.role === 'system') {
    return <SystemMessage message={message} />
  }

  if (message.role === 'error') {
    return <ErrorBubble message={message} onRetry={onRetry} />
  }

  const isUser = message.role === 'user'
  const hasTrace =
    !isUser &&
    ((message.toolCalls?.length ?? 0) > 0 ||
      message.metadata !== undefined ||
      isStreaming === true)

  const { text, images } = splitMessageContent(message.content)

  return (
    <div className={`flex w-full mb-6 ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-[80%] ${isUser ? 'items-end' : 'items-start'} min-w-0`}>
        <div className={`flex items-center gap-2 mb-1 ${isUser ? 'justify-end' : 'justify-start'}`}>
          <span className="text-xs font-medium text-[var(--c-text-secondary)] uppercase tracking-wide">
            {isUser ? 'You' : '🐾 PyClaw'}
          </span>
        </div>

        {hasTrace && (
          <ExecutionTrace
            toolCalls={message.toolCalls}
            metadata={message.metadata}
            isStreaming={isStreaming}
          />
        )}

        {images.length > 0 && <ImageGrid images={images} alignRight={isUser} />}

        {(text.length > 0 || (!isUser && isStreaming)) && (
          <div
            className={
              isUser
                ? 'rounded-2xl rounded-br-md px-4 py-3 bg-[var(--c-msg-user-bg)] border border-[var(--c-border)] text-sm leading-relaxed text-[var(--c-text)]'
                : 'text-sm leading-relaxed text-[var(--c-text)]'
            }
          >
            {isUser ? (
              <div className="whitespace-pre-wrap break-words">
                {renderPlainText(text)}
              </div>
            ) : (
              <MarkdownRenderer content={text} className="break-words [&_code]:font-mono" />
            )}

            {isStreaming && (
              <span className="inline-block w-1.5 h-4 bg-[var(--c-text)] opacity-70 animate-pulse ml-0.5 -mb-0.5 rounded-sm" />
            )}
          </div>
        )}
      </div>
    </div>
  )
}
