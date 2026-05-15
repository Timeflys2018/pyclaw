import { useCallback, useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { useApprovalStore } from '../stores/approval'
import type {
  Message,
  MessageMetadata,
  ToolCallInfo,
  WSClientMessage,
  WSServerMessage,
  WSState,
} from '../types'

const FAILED_RECONNECTS_BEFORE_BANNER = 3

interface UseChatSocketResult {
  wsState: WSState
  send: (msg: WSClientMessage) => void
  failedReconnects: number
  forceReconnect: () => void
}

export function useChatSocket(token: string | null): UseChatSocketResult {
  const { wsState, send, lastMessage, conversations, setConversations } =
    useWebSocket(token)

  const setConvList = useSessionStore((s) => s.setConversations)
  const failedReconnectsRef = useRef(0)
  const failedReconnectsTickRef = useRef(0)
  const lastWsStateRef = useRef<WSState>('disconnected')
  const streamStartRef = useRef<number | null>(null)

  useEffect(() => {
    setConvList(conversations)
  }, [conversations, setConvList])

  useEffect(() => {
    const prev = lastWsStateRef.current
    lastWsStateRef.current = wsState

    if (wsState === 'ready') {
      if (failedReconnectsRef.current !== 0) {
        failedReconnectsRef.current = 0
        failedReconnectsTickRef.current += 1
      }
      return
    }

    if (
      wsState === 'disconnected' &&
      (prev === 'ready' || prev === 'connecting' || prev === 'identifying')
    ) {
      failedReconnectsRef.current += 1
      failedReconnectsTickRef.current += 1
    }
  }, [wsState])

  const dispatchServerMessage = useCallback((msg: WSServerMessage) => {
    const chat = useChatStore.getState()
    const session = useSessionStore.getState()
    const approval = useApprovalStore.getState()
    const convId =
      'conversation_id' in msg ? msg.conversation_id : null

    switch (msg.type) {
      case 'chat.delta':
        if (streamStartRef.current === null) {
          streamStartRef.current = Date.now()
        }
        chat.appendDelta(msg.data.text)
        break

      case 'chat.tool_start': {
        const tc: ToolCallInfo = {
          id: msg.data.tool_call_id,
          name: msg.data.name,
          args: msg.data.args,
          status: 'running',
        }
        chat.beginToolCall(tc)
        break
      }

      case 'chat.tool_end':
        chat.finishToolCall(msg.data.tool_call_id, msg.data.result, 'done')
        break

      case 'chat.done': {
        const startedAt = streamStartRef.current
        streamStartRef.current = null
        const metadata = buildMetadata(startedAt, msg.data)

        if (convId) {
          const aborted = msg.data.aborted === true
          const finalMessage = msg.data.final_message
          const finalText =
            typeof finalMessage === 'string'
              ? finalMessage
              : finalMessage?.content ?? ''
          const partial = chat.getStreamingText()
          const pendingTools = chat.takePendingToolCalls()

          if (aborted && partial) {
            const partialMsg: Message = {
              id: `asst_partial_${Date.now()}`,
              role: 'assistant',
              content: partial,
              timestamp: Date.now(),
              toolCalls: pendingTools.length > 0 ? pendingTools : undefined,
              metadata,
            }
            chat.appendMessage(convId, partialMsg)
          } else if (finalText.trim().length > 0) {
            const finalMsg: Message = {
              id: `asst_${Date.now()}`,
              role: 'assistant',
              content: finalText,
              timestamp: Date.now(),
              toolCalls: pendingTools.length > 0 ? pendingTools : undefined,
              metadata,
            }
            chat.appendMessage(convId, finalMsg)
          }
        } else {
          chat.takePendingToolCalls()
        }
        chat.clearStreaming()
        break
      }

      case 'chat.queued':
        chat.setQueued(true, msg.data.position)
        break

      case 'tool.approve_request':
        if (convId) {
          approval.setPendingApproval({
            conversationId: convId,
            toolCallId: msg.data.tool_call_id,
            toolName: msg.data.tool_name,
            args: msg.data.args,
            reason: msg.data.reason,
          })
        }
        break

      case 'error': {
        streamStartRef.current = null
        if (convId) {
          const partial = chat.getStreamingText()
          const pendingTools = chat.takePendingToolCalls()
          if (partial) {
            const partialMsg: Message = {
              id: `asst_partial_${Date.now()}`,
              role: 'assistant',
              content: partial,
              timestamp: Date.now(),
              toolCalls: pendingTools.length > 0 ? pendingTools : undefined,
            }
            chat.appendMessage(convId, partialMsg)
          }
          const errMsg: Message = {
            id: `err_${Date.now()}`,
            role: 'error',
            content: msg.data.message || 'Internal error',
            timestamp: Date.now(),
          }
          chat.appendMessage(convId, errMsg)
        } else {
          chat.takePendingToolCalls()
        }
        chat.clearStreaming()
        break
      }

      case 'ready':
        if (msg.data.conversations) {
          session.setConversations(msg.data.conversations)
          setConversations(msg.data.conversations)
        }
        break

      default:
        break
    }
  }, [setConversations])

  useEffect(() => {
    if (lastMessage) dispatchServerMessage(lastMessage)
  }, [lastMessage, dispatchServerMessage])

  const forceReconnect = useCallback(() => {
    failedReconnectsRef.current = 0
    failedReconnectsTickRef.current += 1
    if (typeof window !== 'undefined') {
      window.location.reload()
    }
  }, [])

  return {
    wsState,
    send,
    failedReconnects: failedReconnectsRef.current,
    forceReconnect,
  }
}

export { FAILED_RECONNECTS_BEFORE_BANNER }

function buildMetadata(
  startedAt: number | null,
  data: { usage?: { input?: number; output?: number; cache_read?: number }; model?: string },
): MessageMetadata | undefined {
  const meta: MessageMetadata = {}
  if (startedAt !== null) {
    meta.durationMs = Math.max(0, Date.now() - startedAt)
  }
  if (data.usage) {
    meta.usage = {
      input: data.usage.input,
      output: data.usage.output,
      cacheRead: data.usage.cache_read,
    }
  }
  if (data.model) {
    meta.model = data.model
  }
  if (
    meta.durationMs === undefined &&
    meta.usage === undefined &&
    meta.model === undefined
  ) {
    return undefined
  }
  return meta
}
