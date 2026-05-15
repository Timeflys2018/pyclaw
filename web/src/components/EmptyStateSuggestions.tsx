import { Sparkles, Brain, HelpCircle } from 'lucide-react'

interface SuggestionPrompt {
  label: string
  prompt: string
  icon: typeof Sparkles
}

const SUGGESTIONS: SuggestionPrompt[] = [
  {
    label: 'Debug a Python error',
    prompt: '帮我看看这个 Python 错误：\n\n```\n# paste your traceback here\n```',
    icon: Sparkles,
  },
  {
    label: 'Explain PyClaw memory',
    prompt: '解释一下 PyClaw 的 4 层记忆系统是怎么工作的。',
    icon: Brain,
  },
  {
    label: 'List slash commands',
    prompt: '/help',
    icon: HelpCircle,
  },
]

interface Props {
  onPick: (prompt: string) => void
}

export default function EmptyStateSuggestions({ onPick }: Props) {
  return (
    <div className="h-full flex flex-col items-center justify-center text-center min-h-[60vh] gap-6 px-4">
      <div className="p-4 rounded-2xl bg-[var(--c-surface)] border border-[var(--c-border)]">
        <span className="text-3xl">🐾</span>
      </div>
      <div className="space-y-1">
        <h2 className="text-lg font-display font-semibold text-[var(--c-text)]">
          What can I help with?
        </h2>
        <p className="text-sm text-[var(--c-text-secondary)] max-w-xs mx-auto">
          Send a message, or pick a starting point below.
        </p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2 w-full max-w-2xl">
        {SUGGESTIONS.map((s) => {
          const Icon = s.icon
          return (
            <button
              key={s.label}
              type="button"
              onClick={() => onPick(s.prompt)}
              className="flex items-center gap-2 px-3 py-2.5 rounded-lg border border-[var(--c-border)]
                         bg-[var(--c-surface)]/40 hover:bg-[var(--c-surface)] hover:border-[var(--c-accent)]/40
                         text-xs text-left text-[var(--c-text)] transition-colors cursor-pointer"
            >
              <Icon size={14} className="text-[var(--c-text-secondary)] shrink-0" />
              <span className="truncate">{s.label}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
