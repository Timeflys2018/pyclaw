import type { HighlighterCore } from 'shiki/core'

const SUPPORTED_LANGS = [
  'typescript',
  'tsx',
  'javascript',
  'jsx',
  'python',
  'bash',
  'shell',
  'json',
  'yaml',
  'markdown',
  'rust',
  'go',
  'java',
  'cpp',
  'c',
  'sql',
  'html',
  'css',
  'diff',
] as const

const LANG_ALIASES: Record<string, string> = {
  ts: 'typescript',
  js: 'javascript',
  py: 'python',
  sh: 'bash',
  zsh: 'bash',
  yml: 'yaml',
  md: 'markdown',
  rs: 'rust',
}

let cachedHighlighter: Promise<HighlighterCore> | null = null

export function loadHighlighter(): Promise<HighlighterCore> {
  if (cachedHighlighter) return cachedHighlighter
  cachedHighlighter = (async () => {
    const { createHighlighterCore } = await import('shiki/core')
    const { createOnigurumaEngine } = await import('shiki/engine/oniguruma')

    const langImports = SUPPORTED_LANGS.map(
      (lang) => import(`shiki/langs/${lang}.mjs`),
    )
    const themeImports = [
      import('shiki/themes/github-dark.mjs'),
      import('shiki/themes/github-light.mjs'),
    ]

    const [langs, themes] = await Promise.all([
      Promise.all(langImports),
      Promise.all(themeImports),
    ])

    return createHighlighterCore({
      themes,
      langs,
      engine: createOnigurumaEngine(import('shiki/wasm')),
    })
  })()
  return cachedHighlighter
}

export function normalizeLang(lang: string | null | undefined): string {
  if (!lang) return 'text'
  const lower = lang.toLowerCase().trim()
  if (LANG_ALIASES[lower]) return LANG_ALIASES[lower]
  if ((SUPPORTED_LANGS as readonly string[]).includes(lower)) return lower
  return 'text'
}

export function isLangSupported(lang: string): boolean {
  return lang === 'text' || (SUPPORTED_LANGS as readonly string[]).includes(lang)
}
