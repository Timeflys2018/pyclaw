import { type FC, memo, useState, useCallback } from 'react'
import Markdown, { type Components, type ExtraProps } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Copy, Check } from 'lucide-react'

type CodeProps = React.HTMLAttributes<HTMLElement> & ExtraProps

function CodeBlock({ children, className, node, ...props }: CodeProps) {
  const [copied, setCopied] = useState(false)
  const match = /language-(\w+)/.exec(className || '')
  const isInline = !match && node?.position?.start.line === node?.position?.end.line

  const handleCopy = useCallback(async () => {
    const text = String(children).replace(/\n$/, '')
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [children])

  if (isInline) {
    return (
      <code
        className="px-1.5 py-0.5 rounded bg-[var(--c-code-bg)] text-xs font-mono"
        {...props}
      >
        {children}
      </code>
    )
  }

  const language = match?.[1] ?? ''

  return (
    <div className="relative group my-3 rounded-lg overflow-hidden bg-[var(--c-code-bg)] border border-[var(--c-border)]">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-[var(--c-border)]">
        <span className="text-[10px] uppercase tracking-wider font-semibold text-[var(--c-text-secondary)]">
          {language || 'code'}
        </span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-[10px] text-[var(--c-text-secondary)] hover:text-[var(--c-text)]
                     transition-colors cursor-pointer"
        >
          {copied ? (
            <>
              <Check size={12} />
              <span>Copied!</span>
            </>
          ) : (
            <>
              <Copy size={12} />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <pre className="overflow-x-auto p-3 text-xs leading-relaxed">
        <code className={className} {...props}>
          {children}
        </code>
      </pre>
    </div>
  )
}

const components: Components = {
  code: CodeBlock,

  a({ href, children, ...props }) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-[var(--c-accent)] hover:underline"
        {...props}
      >
        {children}
      </a>
    )
  },

  h1: ({ children }) => <h1 className="text-xl font-bold mt-5 mb-2 text-[var(--c-text)]">{children}</h1>,
  h2: ({ children }) => <h2 className="text-lg font-bold mt-4 mb-2 text-[var(--c-text)]">{children}</h2>,
  h3: ({ children }) => <h3 className="text-base font-semibold mt-3 mb-1 text-[var(--c-text)]">{children}</h3>,
  h4: ({ children }) => <h4 className="text-sm font-semibold mt-3 mb-1 text-[var(--c-text)]">{children}</h4>,
  h5: ({ children }) => <h5 className="text-sm font-medium mt-2 mb-1 text-[var(--c-text)]">{children}</h5>,
  h6: ({ children }) => <h6 className="text-xs font-medium mt-2 mb-1 text-[var(--c-text-secondary)]">{children}</h6>,

  ul: ({ children }) => <ul className="list-disc pl-5 my-2 space-y-0.5">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal pl-5 my-2 space-y-0.5">{children}</ol>,
  li: ({ children }) => <li className="text-sm leading-relaxed">{children}</li>,

  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-[var(--c-accent)] pl-3 my-2 text-[var(--c-text-secondary)] italic">
      {children}
    </blockquote>
  ),

  table: ({ children }) => (
    <div className="overflow-x-auto my-3">
      <table className="min-w-full border-collapse text-sm">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-[var(--c-code-bg)]">{children}</thead>,
  th: ({ children }) => (
    <th className="border border-[var(--c-border)] px-3 py-1.5 text-left font-semibold text-xs">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-[var(--c-border)] px-3 py-1.5 text-sm">
      {children}
    </td>
  ),

  hr: () => <hr className="my-4 border-[var(--c-border)]" />,

  p: ({ children }) => <p className="my-1.5 leading-relaxed">{children}</p>,
}

interface MarkdownRendererProps {
  content: string
  className?: string
}

const MarkdownRenderer: FC<MarkdownRendererProps> = memo(({ content, className }) => (
  <div className={className}>
    <Markdown remarkPlugins={[remarkGfm]} components={components}>
      {content}
    </Markdown>
  </div>
))

MarkdownRenderer.displayName = 'MarkdownRenderer'

export default MarkdownRenderer
