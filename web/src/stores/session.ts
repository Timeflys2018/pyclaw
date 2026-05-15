import { create } from 'zustand'
import type { Conversation } from '../types'

interface SessionState {
  conversations: Conversation[]
  activeConvId: string | null
}

interface SessionActions {
  setConversations: (conversations: Conversation[]) => void
  updateConversations: (updater: (prev: Conversation[]) => Conversation[]) => void
  prependConversation: (conv: Conversation) => void
  removeConversation: (id: string) => string | null
  setActiveConvId: (id: string | null) => void
}

type SessionStore = SessionState & SessionActions

export const useSessionStore = create<SessionStore>((set, get) => ({
  conversations: [],
  activeConvId: null,

  setConversations: (conversations) => set({ conversations }),

  updateConversations: (updater) =>
    set((state) => ({ conversations: updater(state.conversations) })),

  prependConversation: (conv) =>
    set((state) => {
      if (state.conversations.some((c) => c.id === conv.id)) {
        return state
      }
      return { conversations: [conv, ...state.conversations] }
    }),

  removeConversation: (id) => {
    const { conversations, activeConvId } = get()
    const remaining = conversations.filter((c) => c.id !== id)
    set({ conversations: remaining })
    if (activeConvId === id) {
      return remaining[0]?.id ?? null
    }
    return activeConvId
  },

  setActiveConvId: (id) => set({ activeConvId: id }),
}))
