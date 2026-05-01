import { useRef, useState, useEffect, useCallback } from 'react'
import type {
  WSState,
  WSServerMessage,
  WSClientMessage,
  Conversation,
} from '../types'

const MIN_BACKOFF = 500
const MAX_BACKOFF = 30_000

function jitter(ms: number): number {
  return ms + Math.random() * ms * 0.3
}

export function useWebSocket(token: string | null) {
  const [wsState, setWsState] = useState<WSState>('disconnected')
  const [lastMessage, setLastMessage] = useState<WSServerMessage | null>(null)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const wsRef = useRef<WebSocket | null>(null)
  const backoffRef = useRef(MIN_BACKOFF)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const tokenRef = useRef(token)
  tokenRef.current = token

  const send = useCallback((msg: WSClientMessage) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg))
    }
  }, [])

  const connect = useCallback(() => {
    if (!tokenRef.current) return
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }

    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${window.location.host}/api/ws`)
    wsRef.current = ws
    setWsState('connecting')

    ws.onopen = () => {
      backoffRef.current = MIN_BACKOFF
    }

    ws.onmessage = (ev) => {
      let msg: WSServerMessage
      try {
        msg = JSON.parse(ev.data)
      } catch {
        return
      }

      switch (msg.type) {
        case 'hello':
          setWsState('identifying')
          send({ type: 'identify', token: tokenRef.current! })
          break
        case 'ready':
          setWsState('ready')
          setConversations(msg.data.conversations)
          break
        case 'ping':
          send({ type: 'pong' })
          break
        default:
          break
      }

      setLastMessage(msg)
    }

    ws.onclose = () => {
      wsRef.current = null
      setWsState('disconnected')
      if (tokenRef.current) {
        const delay = jitter(backoffRef.current)
        backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF)
        reconnectTimer.current = setTimeout(connect, delay)
      }
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [send])

  useEffect(() => {
    if (token) {
      connect()
    } else {
      wsRef.current?.close()
      setWsState('disconnected')
    }
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [token, connect])

  return { wsState, send, lastMessage, conversations, setConversations }
}
