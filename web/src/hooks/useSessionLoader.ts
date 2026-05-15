import { useCallback, useEffect } from 'react'
import { useSessionStore } from '../stores/session'
import { useChatStore } from '../stores/chat'
import type { ContentBlock, Conversation, Message, WSState } from '../types'

interface SessionListResponse {
  id: string
  created_at?: string
  message_count?: number
  title?: string | null
  last_interaction_at?: string | null
}

interface MessageHistoryEntry {
  role: string
  content?: string | ContentBlock[]
  id?: string
  timestamp?: string
}

function normalizeContent(raw: unknown): string | ContentBlock[] {
  if (typeof raw === 'string') return raw
  if (!Array.isArray(raw)) return ''
  const blocks: ContentBlock[] = []
  for (const item of raw) {
    if (item && typeof item === 'object') {
      const block = item as Record<string, unknown>
      if (block.type === 'text' && typeof block.text === 'string') {
        blocks.push({ type: 'text', text: block.text })
      } else if (
        (block.type === 'image' || block.type === 'image_url') &&
        typeof block.data === 'string' &&
        typeof block.mime_type === 'string'
      ) {
        blocks.push({ type: 'image', data: block.data, mime_type: block.mime_type })
      }
    }
  }
  return blocks
}

export function useSessionLoader(token: string | null, wsState: WSState) {
  const setConversations = useSessionStore((s) => s.setConversations)
  const setMessages = useChatStore((s) => s.setMessages)

  useEffect(() => {
    if (wsState !== 'ready' || !token) return
    fetch('/api/sessions', { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.json() : []))
      .then((sessions: SessionListResponse[]) => {
        if (sessions.length === 0) return
        const loaded: Conversation[] = sessions.map((s) => ({
          id: s.id,
          title: s.title || 'New chat',
          updatedAt: s.last_interaction_at
            ? new Date(s.last_interaction_at).getTime()
            : s.created_at
              ? new Date(s.created_at).getTime()
              : Date.now(),
          active: true,
        }))
        setConversations(loaded)
      })
      .catch(() => {})
  }, [wsState, token, setConversations])

  const loadMessagesFor = useCallback(
    async (convId: string): Promise<void> => {
      try {
        const res = await fetch(
          `/api/sessions/${encodeURIComponent(convId)}/messages?limit=100`,
          { headers: { Authorization: `Bearer ${token}` } },
        )
        if (!res.ok) return
        const entries: MessageHistoryEntry[] = await res.json()
        if (!Array.isArray(entries)) return
        const serverMsgs: Message[] = entries.map((e) => ({
          id: e.id ?? `msg_${Math.random().toString(36).slice(2)}`,
          role: e.role === 'user' ? 'user' : 'assistant',
          content: normalizeContent(e.content),
          timestamp: e.timestamp ? new Date(e.timestamp).getTime() : Date.now(),
        }))
        const local = useChatStore.getState().messagesByConv[convId] ?? []
        if (serverMsgs.length === 0 && local.length > 0) return
        setMessages(convId, serverMsgs)
      } catch {}
    },
    [setMessages, token],
  )

  return { loadMessagesFor }
}
