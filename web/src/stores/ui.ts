import { create } from 'zustand'
import type { Theme } from '../types'

const THEME_KEY = 'pyclaw-theme'

function loadInitialTheme(): Theme {
  if (typeof window === 'undefined') return 'dark'
  const stored = window.localStorage.getItem(THEME_KEY)
  return stored === 'light' ? 'light' : 'dark'
}

function applyThemeToDOM(theme: Theme): void {
  if (typeof document === 'undefined') return
  document.documentElement.setAttribute('data-theme', theme)
  try {
    window.localStorage.setItem(THEME_KEY, theme)
  } catch {
    // Safari private mode rejects setItem; the DOM attribute is already
    // applied so theme works for the current session, just won't persist.
  }
}

interface UiState {
  theme: Theme
  sidebarCollapsed: boolean
}

interface UiActions {
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
  setSidebarCollapsed: (collapsed: boolean) => void
  toggleSidebar: () => void
}

type UiStore = UiState & UiActions

const initialTheme = loadInitialTheme()
applyThemeToDOM(initialTheme)

export const useUiStore = create<UiStore>((set, get) => ({
  theme: initialTheme,
  sidebarCollapsed: false,

  setTheme: (theme) => {
    applyThemeToDOM(theme)
    set({ theme })
  },

  toggleTheme: () => {
    const next: Theme = get().theme === 'dark' ? 'light' : 'dark'
    applyThemeToDOM(next)
    set({ theme: next })
  },

  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),

  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
}))
