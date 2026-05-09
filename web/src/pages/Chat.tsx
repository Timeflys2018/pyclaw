import { useState, useEffect, useCallback, useRef } from 'react'
import { LogOut, Wifi, WifiOff } from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import { useWebSocket } from '../hooks/useWebSocket'
import SessionSidebar from '../components/SessionSidebar'
import ChatArea from '../components/ChatArea'
import ThemeToggle from '../components/ThemeToggle'
import ToolApprovalModal from '../components/ToolApproval'
import type {
  Theme,
  Message,
  ToolCallInfo,
  PendingApproval,
  WSServerMessage,
} from '../types'

function loadTheme(): Theme {
  return (localStorage.getItem('pyclaw-theme') as Theme) ?? 'dark'
}

export default function Chat() {
  const { token, userId, logout } = useAuth()
  const { wsState, send, lastMessage, conversations, setConversations } =
    useWebSocket(token)

  const [theme, setTheme] = useState<Theme>(loadTheme)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [activeConvId, setActiveConvId] = useState<string | null>(null)

  const [messagesByConv, setMessagesByConv] = useState<Record<string, Message[]>>({})
  const [streamingText, setStreamingText] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [isQueued, setIsQueued] = useState(false)
  const [queuePosition, setQueuePosition] = useState(0)
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null)

  const toolCallsRef = useRef<ToolCallInfo[]>([])
  const streamingTextRef = useRef('')

  const currentMessages = activeConvId ? messagesByConv[activeConvId] ?? [] : []

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('pyclaw-theme', theme)
  }, [theme])

  useEffect(() => {
    if (wsState === 'ready' && token) {
      fetch('/api/sessions', { headers: { Authorization: `Bearer ${token}` } })
        .then((r) => r.ok ? r.json() : [])
        .then((sessions: Array<{ id: string; created_at?: string; message_count?: number; title?: string | null; last_interaction_at?: string | null }>) => {
          if (sessions.length > 0) {
            const loaded = sessions.map((s) => ({
              id: s.id,
              title: s.title || 'New chat',
              updatedAt: s.last_interaction_at
                ? new Date(s.last_interaction_at).getTime()
                : s.created_at ? new Date(s.created_at).getTime() : Date.now(),
              active: true,
            }))
            setConversations(loaded)
          }
        })
        .catch(() => {})
    }
  }, [wsState, token, setConversations])

  useEffect(() => {
    if (conversations.length > 0 && !activeConvId) {
      setActiveConvId(conversations[0].id)
    }
  }, [conversations, activeConvId])

  const appendMessage = useCallback(
    (convId: string, msg: Message) => {
      setMessagesByConv((prev) => ({
        ...prev,
        [convId]: [...(prev[convId] ?? []), msg],
      }))
    },
    []
  )

  useEffect(() => {
    if (!lastMessage) return

    const msg: WSServerMessage = lastMessage
    const convId = 'conversation_id' in msg ? (msg as { conversation_id: string }).conversation_id : null

    switch (msg.type) {
      case 'chat.delta':
        setIsQueued(false)
        setIsStreaming(true)
        streamingTextRef.current = streamingTextRef.current + msg.data.text
        setStreamingText(streamingTextRef.current)
        break

      case 'chat.tool_start': {
        const tc: ToolCallInfo = {
          id: msg.data.tool_call_id,
          name: msg.data.name,
          args: msg.data.args,
          status: 'running',
        }
        toolCallsRef.current = [...toolCallsRef.current, tc]
        break
      }

      case 'chat.tool_end': {
        toolCallsRef.current = toolCallsRef.current.map((tc) =>
          tc.id === msg.data.tool_call_id
            ? { ...tc, result: msg.data.result, status: 'done' as const }
            : tc
        )
        break
      }

      case 'chat.done': {
        if (convId) {
          const aborted = msg.data.aborted === true
          const finalText = typeof msg.data.final_message === 'string'
            ? msg.data.final_message
            : msg.data.final_message?.content ?? ''
          const partial = streamingTextRef.current

          if (aborted && partial) {
            const partialMsg: Message = {
              id: `asst_partial_${Date.now()}`,
              role: 'assistant',
              content: partial,
              timestamp: Date.now(),
              toolCalls:
                toolCallsRef.current.length > 0
                  ? [...toolCallsRef.current]
                  : undefined,
            }
            appendMessage(convId, partialMsg)
          }

          const shouldShowFinal = finalText.trim().length > 0
          if (shouldShowFinal) {
            const finalMsg: Message = {
              id: `asst_${Date.now()}`,
              role: 'assistant',
              content: finalText,
              timestamp: Date.now(),
              toolCalls:
                !aborted && toolCallsRef.current.length > 0
                  ? [...toolCallsRef.current]
                  : undefined,
            }
            appendMessage(convId, finalMsg)
          }
        }
        streamingTextRef.current = ''
        setStreamingText('')
        setIsStreaming(false)
        setIsQueued(false)
        toolCallsRef.current = []
        break
      }

      case 'chat.queued':
        setIsQueued(true)
        setQueuePosition(msg.data.position)
        break

      case 'tool.approve_request':
        if (convId) {
          setPendingApproval({
            conversationId: convId,
            toolCallId: msg.data.tool_call_id,
            toolName: msg.data.tool_name,
            args: msg.data.args,
            reason: msg.data.reason,
          })
        }
        break

      case 'error': {
        console.error('[ws error]', msg.data.message)
        if (convId) {
          const partial = streamingTextRef.current
          if (partial) {
            const partialMsg: Message = {
              id: `asst_partial_${Date.now()}`,
              role: 'assistant',
              content: partial,
              timestamp: Date.now(),
              toolCalls:
                toolCallsRef.current.length > 0
                  ? [...toolCallsRef.current]
                  : undefined,
            }
            appendMessage(convId, partialMsg)
          }
          const errMsg: Message = {
            id: `err_${Date.now()}`,
            role: 'assistant',
            content: `⚠️ ${msg.data.message || 'Internal error'}`,
            timestamp: Date.now(),
          }
          appendMessage(convId, errMsg)
        }
        streamingTextRef.current = ''
        setStreamingText('')
        setIsStreaming(false)
        setIsQueued(false)
        toolCallsRef.current = []
        break
      }
    }
  }, [lastMessage, appendMessage])

  const handleSend = useCallback(
    async (text: string) => {
      let convId = activeConvId
      if (!convId) {
        try {
          const res = await fetch('/api/sessions', {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}` },
          })
          if (res.ok) {
            const data = await res.json()
            convId = data.session_id
          }
        } catch {}
        if (!convId) {
          convId = `conv_${Date.now()}`
        }
        setActiveConvId(convId)
        setConversations((prev) => {
          if (prev.some((c) => c.id === convId)) return prev
          return [
            { id: convId!, title: text.slice(0, 30), updatedAt: Date.now(), active: true },
            ...prev,
          ]
        })
      }

      const userMsg: Message = {
        id: `usr_${Date.now()}`,
        role: 'user',
        content: text,
        timestamp: Date.now(),
      }
      appendMessage(convId, userMsg)
      send({
        type: 'chat.send',
        conversation_id: convId,
        content: text,
      })
    },
    [activeConvId, appendMessage, send, token]
  )

  const handleAbort = useCallback(() => {
    if (activeConvId) {
      send({ type: 'chat.abort', conversation_id: activeConvId })
    }
  }, [activeConvId, send])

  const handleSelectSession = useCallback(async (convId: string) => {
    setActiveConvId(convId)
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(convId)}/messages?limit=100`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) {
        const entries = await res.json()
        if (Array.isArray(entries) && entries.length > 0) {
          const msgs: Message[] = entries.map((e: { role: string; content?: string; id?: string; timestamp?: string }) => ({
            id: e.id ?? `msg_${Math.random().toString(36).slice(2)}`,
            role: e.role === 'user' ? 'user' : 'assistant',
            content: e.content ?? '',
            timestamp: e.timestamp ? new Date(e.timestamp).getTime() : Date.now(),
          }))
          setMessagesByConv((prev) => ({ ...prev, [convId]: msgs }))
        }
      }
    } catch {}
  }, [token])

  const handleNewSession = useCallback(async () => {
    try {
      const res = await fetch('/api/sessions', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) return
      const data = await res.json()
      const newId = data.session_id
      setConversations((prev) => [
        { id: newId, title: newId.split(':').pop()?.slice(0, 8) ?? 'New', updatedAt: Date.now(), active: true },
        ...prev,
      ])
      setActiveConvId(newId)
      streamingTextRef.current = ''
      setStreamingText('')
    } catch {}
  }, [token, setConversations])

  const handleApproval = useCallback(
    (approved: boolean) => {
      if (!pendingApproval) return
      send({
        type: 'tool.approve',
        conversation_id: pendingApproval.conversationId,
        tool_call_id: pendingApproval.toolCallId,
        approved,
      })
      setPendingApproval(null)
    },
    [pendingApproval, send]
  )

  useEffect(() => {
    if (!lastMessage || lastMessage.type !== 'ready') return
    const readyMsg = lastMessage
    if (readyMsg.type === 'ready') {
      setConversations(readyMsg.data.conversations)
    }
  }, [lastMessage, setConversations])

  const wsIndicator = wsState === 'ready'
    ? <Wifi size={14} className="text-[var(--c-success)]" />
    : <WifiOff size={14} className="text-[var(--c-error)]" />

  return (
    <div className="h-screen flex flex-col bg-[var(--c-bg)] text-[var(--c-text)]">
      <header className="flex items-center justify-between h-13 px-4 border-b border-[var(--c-border)] shrink-0 bg-[var(--c-bg)]">
        <div className="flex items-center gap-2">
          <span className="text-sm font-display font-semibold tracking-tight">🐾 PyClaw</span>
          {wsIndicator}
        </div>
        <div className="flex items-center gap-1">
          {userId && (
            <span className="text-xs text-[var(--c-text-secondary)] mr-2 hidden sm:inline">
              {userId}
            </span>
          )}
          <ThemeToggle theme={theme} onChange={setTheme} />
          <button
            onClick={logout}
            className="p-2 rounded-lg hover:bg-[var(--c-surface)] text-[var(--c-text-secondary)]
                       hover:text-[var(--c-text)] transition-colors cursor-pointer"
            title="Sign out"
          >
            <LogOut size={16} />
          </button>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <SessionSidebar
          conversations={conversations}
          activeId={activeConvId}
          onSelect={handleSelectSession}
          onNew={handleNewSession}
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed((c) => !c)}
        />

        <ChatArea
          messages={currentMessages}
          streamingText={streamingText}
          isStreaming={isStreaming}
          isQueued={isQueued}
          queuePosition={queuePosition}
          onSend={handleSend}
          onAbort={handleAbort}
        />
      </div>

      {pendingApproval && (
        <ToolApprovalModal
          approval={pendingApproval}
          onApprove={() => handleApproval(true)}
          onReject={() => handleApproval(false)}
        />
      )}
    </div>
  )
}
