import { create } from 'zustand'
import type { Message, ToolCallInfo } from '../types'

const MAX_CACHED_CONVERSATIONS = 10

interface ChatState {
  messagesByConv: Record<string, Message[]>
  lastAccessAt: Record<string, number>
  streamingText: string
  isStreaming: boolean
  isQueued: boolean
  queuePosition: number
  pendingToolCalls: ToolCallInfo[]
}

interface ChatActions {
  setMessages: (convId: string, messages: Message[]) => void
  appendMessage: (convId: string, message: Message) => void
  touchConversation: (convId: string) => void
  dropConversation: (convId: string) => void
  appendDelta: (delta: string) => void
  getStreamingText: () => string
  clearStreaming: () => void
  setQueued: (queued: boolean, position?: number) => void
  beginToolCall: (call: ToolCallInfo) => void
  finishToolCall: (id: string, result: string, status?: 'done' | 'error') => void
  takePendingToolCalls: () => ToolCallInfo[]
  clearPendingToolCalls: () => void
}

type ChatStore = ChatState & ChatActions

function evictLeastRecentlyAccessed(
  messagesByConv: Record<string, Message[]>,
  lastAccessAt: Record<string, number>,
  protectedConvId?: string,
): { messagesByConv: Record<string, Message[]>; lastAccessAt: Record<string, number> } {
  const keys = Object.keys(messagesByConv)
  if (keys.length <= MAX_CACHED_CONVERSATIONS) {
    return { messagesByConv, lastAccessAt }
  }

  let lruKey: string | null = null
  let lruAt = Number.POSITIVE_INFINITY
  for (const key of keys) {
    if (key === protectedConvId) continue
    const at = lastAccessAt[key] ?? 0
    if (at < lruAt) {
      lruAt = at
      lruKey = key
    }
  }
  if (lruKey === null) {
    return { messagesByConv, lastAccessAt }
  }

  const nextMessages = { ...messagesByConv }
  const nextAccess = { ...lastAccessAt }
  delete nextMessages[lruKey]
  delete nextAccess[lruKey]
  return { messagesByConv: nextMessages, lastAccessAt: nextAccess }
}

export const useChatStore = create<ChatStore>((set, get) => ({
  messagesByConv: {},
  lastAccessAt: {},
  streamingText: '',
  isStreaming: false,
  isQueued: false,
  queuePosition: 0,
  pendingToolCalls: [],

  setMessages: (convId, messages) =>
    set((state) => {
      const messagesByConv = { ...state.messagesByConv, [convId]: messages }
      const lastAccessAt = { ...state.lastAccessAt, [convId]: Date.now() }
      return evictLeastRecentlyAccessed(messagesByConv, lastAccessAt, convId)
    }),

  appendMessage: (convId, message) =>
    set((state) => {
      const existing = state.messagesByConv[convId] ?? []
      const messagesByConv = {
        ...state.messagesByConv,
        [convId]: [...existing, message],
      }
      const lastAccessAt = { ...state.lastAccessAt, [convId]: Date.now() }
      return evictLeastRecentlyAccessed(messagesByConv, lastAccessAt, convId)
    }),

  touchConversation: (convId) =>
    set((state) => ({
      lastAccessAt: { ...state.lastAccessAt, [convId]: Date.now() },
    })),

  dropConversation: (convId) =>
    set((state) => {
      const messagesByConv = { ...state.messagesByConv }
      const lastAccessAt = { ...state.lastAccessAt }
      delete messagesByConv[convId]
      delete lastAccessAt[convId]
      return { messagesByConv, lastAccessAt }
    }),

  appendDelta: (delta) =>
    set((state) => ({
      streamingText: state.streamingText + delta,
      isStreaming: true,
      isQueued: false,
    })),

  getStreamingText: () => get().streamingText,

  clearStreaming: () =>
    set({
      streamingText: '',
      isStreaming: false,
      isQueued: false,
      queuePosition: 0,
      pendingToolCalls: [],
    }),

  setQueued: (queued, position = 0) =>
    set({ isQueued: queued, queuePosition: position }),

  beginToolCall: (call) =>
    set((state) => ({ pendingToolCalls: [...state.pendingToolCalls, call] })),

  finishToolCall: (id, result, status = 'done') =>
    set((state) => ({
      pendingToolCalls: state.pendingToolCalls.map((tc) =>
        tc.id === id ? { ...tc, result, status } : tc,
      ),
    })),

  takePendingToolCalls: () => {
    const { pendingToolCalls } = get()
    set({ pendingToolCalls: [] })
    return pendingToolCalls
  },

  clearPendingToolCalls: () => set({ pendingToolCalls: [] }),
}))
