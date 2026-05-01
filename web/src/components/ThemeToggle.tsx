import { Moon, Sun } from 'lucide-react'
import type { Theme } from '../types'

interface Props {
  theme: Theme
  onChange: (t: Theme) => void
}

export default function ThemeToggle({ theme, onChange }: Props) {
  return (
    <button
      onClick={() => onChange(theme === 'dark' ? 'light' : 'dark')}
      className="p-2 rounded-lg hover:bg-[var(--c-surface)] text-[var(--c-text-secondary)]
                 hover:text-[var(--c-text)] transition-colors cursor-pointer"
      aria-label="Toggle theme"
    >
      {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  )
}
