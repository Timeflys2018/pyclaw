import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { LogOut, Wifi, WifiOff } from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import { useChatSocket, FAILED_RECONNECTS_BEFORE_BANNER } from '../hooks/useChatSocket'
import { useSessionLoader } from '../hooks/useSessionLoader'
import { useGlobalKeyboard, type ShortcutBinding } from '../hooks/useGlobalKeyboard'
import {
  useChatStore,
  useSessionStore,
  useUiStore,
  useApprovalStore,
} from '../stores'
import SessionSidebar from '../components/SessionSidebar'
import ChatArea from '../components/ChatArea'
import ThemeToggle from '../components/ThemeToggle'
import ToolApprovalModal from '../components/ToolApproval'
import ErrorBanner from '../components/ErrorBanner'
import CommandPalette, { type PaletteSelection } from '../components/CommandPalette'
import ShortcutsModal from '../components/ShortcutsModal'
import type { Message } from '../types'

export default function Chat() {
  const { token, userId, logout } = useAuth()
  const { wsState, send, failedReconnects, forceReconnect } = useChatSocket(token)
  const { loadMessagesFor } = useSessionLoader(token, wsState)
  const [loadingHistoryFor, setLoadingHistoryFor] = useState<string | null>(null)
  const [isCreatingSession, setIsCreatingSession] = useState(false)
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const [pendingInputPrefill, setPendingInputPrefill] = useState<{ text: string; nonce: number } | null>(null)
  const handleNewSessionRef = useRef<() => void>(() => {})
  const toggleThemeFn = useUiStore((s) => s.toggleTheme)

  const messagesByConv = useChatStore((s) => s.messagesByConv)
  const streamingText = useChatStore((s) => s.streamingText)
  const isStreaming = useChatStore((s) => s.isStreaming)
  const isQueued = useChatStore((s) => s.isQueued)
  const queuePosition = useChatStore((s) => s.queuePosition)
  const appendMessage = useChatStore((s) => s.appendMessage)
  const clearStreamingState = useChatStore((s) => s.clearStreaming)

  const conversations = useSessionStore((s) => s.conversations)
  const activeConvId = useSessionStore((s) => s.activeConvId)
  const setActiveConvId = useSessionStore((s) => s.setActiveConvId)
  const prependConversation = useSessionStore((s) => s.prependConversation)

  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)
  const sidebarCollapsed = useUiStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useUiStore((s) => s.toggleSidebar)

  const pendingApproval = useApprovalStore((s) => s.pendingApproval)
  const clearPendingApproval = useApprovalStore((s) => s.clearPendingApproval)

  const currentMessages = activeConvId ? messagesByConv[activeConvId] ?? [] : []

  useEffect(() => {
    if (conversations.length > 0 && !activeConvId) {
      setActiveConvId(conversations[0].id)
    }
  }, [conversations, activeConvId, setActiveConvId])

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
        prependConversation({
          id: convId,
          title: text.slice(0, 30),
          updatedAt: Date.now(),
          active: true,
        })
      }

      const userMsg: Message = {
        id: `usr_${Date.now()}`,
        role: 'user',
        content: text,
        timestamp: Date.now(),
      }
      appendMessage(convId, userMsg)
      send({ type: 'chat.send', conversation_id: convId, content: text })
    },
    [activeConvId, appendMessage, prependConversation, send, setActiveConvId, token],
  )

  const handleAbort = useCallback(() => {
    if (activeConvId) {
      send({ type: 'chat.abort', conversation_id: activeConvId })
    }
  }, [activeConvId, send])

  const handleSelectSession = useCallback(
    async (convId: string) => {
      setActiveConvId(convId)
      const cached = useChatStore.getState().messagesByConv[convId]
      if (cached && cached.length > 0) return
      setLoadingHistoryFor(convId)
      try {
        await loadMessagesFor(convId)
      } finally {
        setLoadingHistoryFor((current) => (current === convId ? null : current))
      }
    },
    [loadMessagesFor, setActiveConvId],
  )

  const handleNewSession = useCallback(async () => {
    setIsCreatingSession(true)
    try {
      const res = await fetch('/api/sessions', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) return
      const data = await res.json()
      const newId = data.session_id
      prependConversation({
        id: newId,
        title: 'New chat',
        updatedAt: Date.now(),
        active: true,
      })
      setActiveConvId(newId)
      clearStreamingState()
    } catch {} finally {
      setIsCreatingSession(false)
    }
  }, [clearStreamingState, prependConversation, setActiveConvId, token])

  const handleRetryMessage = useCallback(
    (errorMessageId: string) => {
      if (!activeConvId) return
      const all = useChatStore.getState().messagesByConv[activeConvId] ?? []
      const errIdx = all.findIndex((m) => m.id === errorMessageId)
      if (errIdx < 0) return
      let lastUser: Message | null = null
      for (let i = errIdx - 1; i >= 0; i--) {
        if (all[i].role === 'user') {
          lastUser = all[i]
          break
        }
      }
      if (!lastUser) return
      send({
        type: 'chat.send',
        conversation_id: activeConvId,
        content: lastUser.content,
      })
    },
    [activeConvId, send],
  )

  const handleApproval = useCallback(
    (approved: boolean) => {
      if (!pendingApproval) return
      send({
        type: 'tool.approve',
        conversation_id: pendingApproval.conversationId,
        tool_call_id: pendingApproval.toolCallId,
        approved,
      })
      clearPendingApproval()
    },
    [clearPendingApproval, pendingApproval, send],
  )

  useEffect(() => {
    handleNewSessionRef.current = handleNewSession
  }, [handleNewSession])

  const handlePaletteSelect = useCallback(
    (selection: PaletteSelection) => {
      if (selection.kind === 'session') {
        void handleSelectSession(selection.sessionId)
        return
      }
      if (selection.kind === 'slash') {
        setPendingInputPrefill({ text: `${selection.command} `, nonce: Date.now() })
        return
      }
      switch (selection.action.kind) {
        case 'new-session':
          handleNewSessionRef.current()
          break
        case 'toggle-theme':
          toggleThemeFn()
          break
        case 'toggle-sidebar':
          toggleSidebar()
          break
        case 'show-shortcuts':
          setShortcutsOpen(true)
          break
      }
    },
    [handleSelectSession, toggleSidebar, toggleThemeFn],
  )

  const shortcutBindings = useMemo<ShortcutBinding[]>(
    () => [
      {
        modifier: 'mod',
        key: 'k',
        handler: () => {
          setShortcutsOpen(false)
          setCommandPaletteOpen(true)
        },
      },
      {
        modifier: 'mod',
        key: 'n',
        handler: () => handleNewSessionRef.current(),
      },
      {
        modifier: 'mod',
        key: '\\',
        handler: () => toggleSidebar(),
      },
      {
        modifier: 'mod',
        key: '/',
        handler: () => {
          setCommandPaletteOpen(false)
          setShortcutsOpen(true)
        },
      },
    ],
    [toggleSidebar],
  )

  useGlobalKeyboard(shortcutBindings)

  const wsIndicator =
    wsState === 'ready' ? (
      <Wifi size={14} className="text-[var(--c-success)]" />
    ) : (
      <WifiOff size={14} className="text-[var(--c-error)]" />
    )

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

      <div className="flex flex-1 overflow-hidden relative">
        <ErrorBanner
          visible={failedReconnects >= FAILED_RECONNECTS_BEFORE_BANNER}
          onReconnect={forceReconnect}
        />
        <SessionSidebar
          conversations={conversations}
          activeId={activeConvId}
          onSelect={handleSelectSession}
          onNew={handleNewSession}
          collapsed={sidebarCollapsed}
          onToggle={toggleSidebar}
          isCreatingSession={isCreatingSession}
        />

        <ChatArea
          messages={currentMessages}
          streamingText={streamingText}
          isStreaming={isStreaming}
          isQueued={isQueued}
          queuePosition={queuePosition}
          isLoadingHistory={loadingHistoryFor === activeConvId}
          prefillInput={pendingInputPrefill}
          onSend={handleSend}
          onAbort={handleAbort}
          onRetryMessage={handleRetryMessage}
        />
      </div>

      {pendingApproval && (
        <ToolApprovalModal
          approval={pendingApproval}
          onApprove={() => handleApproval(true)}
          onReject={() => handleApproval(false)}
        />
      )}

      <CommandPalette
        open={commandPaletteOpen}
        conversations={conversations}
        theme={theme}
        onClose={() => setCommandPaletteOpen(false)}
        onSelect={handlePaletteSelect}
      />

      <ShortcutsModal open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
    </div>
  )
}
