import { type FC, memo } from 'react'
import Markdown, { type Components, type ExtraProps } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import HighlightedCodeBlock from './HighlightedCodeBlock'

type CodeProps = React.HTMLAttributes<HTMLElement> & ExtraProps

function CodeBlock({ children, className, node, ...props }: CodeProps) {
  const match = /language-(\w+)/.exec(className || '')
  const isInline = !match && node?.position?.start.line === node?.position?.end.line

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

  const language = match?.[1] ?? null
  const code = String(children).replace(/\n$/, '')
  return <HighlightedCodeBlock code={code} rawLang={language} />
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
