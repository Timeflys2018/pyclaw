import { create } from 'zustand'

export type PermissionTier = 'read-only' | 'approval' | 'yolo'

const STORAGE_KEY = 'pyclaw-permission-tier'
const VALID_TIERS: readonly PermissionTier[] = [
  'read-only',
  'approval',
  'yolo',
] as const

function readStoredTier(): PermissionTier | null {
  if (typeof window === 'undefined') return null
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored && (VALID_TIERS as readonly string[]).includes(stored)) {
      return stored as PermissionTier
    }
  } catch {
    // localStorage unavailable (Safari private mode etc.) — fall through.
  }
  return null
}

function persistTier(tier: PermissionTier): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(STORAGE_KEY, tier)
  } catch {
    // ignore
  }
}

interface PermissionState {
  tier: PermissionTier
  hasUserChoice: boolean
}

interface PermissionActions {
  setTier: (tier: PermissionTier) => void
  applyBackendDefault: (backendDefault: PermissionTier) => void
}

type PermissionStore = PermissionState & PermissionActions

const storedTier = readStoredTier()

export const usePermissionStore = create<PermissionStore>((set, get) => ({
  tier: storedTier ?? 'approval',
  hasUserChoice: storedTier !== null,
  setTier: (tier) => {
    persistTier(tier)
    set({ tier, hasUserChoice: true })
  },
  applyBackendDefault: (backendDefault) => {
    if (get().hasUserChoice) return
    if (!(VALID_TIERS as readonly string[]).includes(backendDefault)) return
    set({ tier: backendDefault })
  },
}))
