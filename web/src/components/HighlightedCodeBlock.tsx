import { useEffect, useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { useUiStore } from '../stores/ui'
import { isLangSupported, loadHighlighter, normalizeLang } from '../lib/highlighter'

interface Props {
  code: string
  rawLang?: string | null
}

export default function HighlightedCodeBlock({ code, rawLang }: Props) {
  const lang = normalizeLang(rawLang)
  const theme = useUiStore((s) => s.theme)
  const [highlightedHtml, setHighlightedHtml] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!isLangSupported(lang) || lang === 'text') {
      setHighlightedHtml(null)
      return
    }
    let cancelled = false
    loadHighlighter()
      .then((highlighter) => {
        if (cancelled) return
        const html = highlighter.codeToHtml(code, {
          lang,
          theme: theme === 'dark' ? 'github-dark' : 'github-light',
        })
        setHighlightedHtml(html)
      })
      .catch(() => {
        if (!cancelled) setHighlightedHtml(null)
      })
    return () => {
      cancelled = true
    }
  }, [code, lang, theme])

  const handleCopy = async () => {
    await navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="relative group my-3 rounded-lg overflow-hidden bg-[var(--c-code-bg)] border border-[var(--c-border)]">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-[var(--c-border)]">
        <span className="text-[10px] uppercase tracking-wider font-semibold text-[var(--c-text-secondary)]">
          {rawLang || lang || 'code'}
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
      {highlightedHtml ? (
        <div
          className="shiki-output overflow-x-auto text-xs leading-relaxed [&_pre]:!bg-transparent [&_pre]:!p-3 [&_pre]:!m-0"
          dangerouslySetInnerHTML={{ __html: highlightedHtml }}
        />
      ) : (
        <pre className="overflow-x-auto p-3 text-xs leading-relaxed">
          <code className={`language-${lang}`}>{code}</code>
        </pre>
      )}
    </div>
  )
}
