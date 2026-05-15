/* ─── Domain Models ─── */

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'error'
  content: string
  timestamp: number
  toolCalls?: ToolCallInfo[]
  metadata?: MessageMetadata
}

export interface ToolCallInfo {
  id: string
  name: string
  args: Record<string, unknown>
  result?: string
  status: 'running' | 'done' | 'error'
}

export interface MessageMetadata {
  durationMs?: number
  usage?: {
    input?: number
    output?: number
    cacheRead?: number
  }
  model?: string
}

export interface Conversation {
  id: string
  title: string
  updatedAt: number
  active: boolean
}

/* ─── WebSocket Protocol ─── */

export type WSState = 'connecting' | 'identifying' | 'ready' | 'disconnected'

// Client → Server
export interface WSClientIdentify {
  type: 'identify'
  token: string
}

export interface WSClientChatSend {
  type: 'chat.send'
  conversation_id: string
  content: string
}

export interface WSClientChatAbort {
  type: 'chat.abort'
  conversation_id: string
}

export interface WSClientToolApprove {
  type: 'tool.approve'
  conversation_id: string
  tool_call_id: string
  approved: boolean
}

export interface WSClientPong {
  type: 'pong'
}

export type WSClientMessage =
  | WSClientIdentify
  | WSClientChatSend
  | WSClientChatAbort
  | WSClientToolApprove
  | WSClientPong

// Server → Client
export interface WSServerHello {
  type: 'hello'
  data: { heartbeat_interval: number }
}

export interface WSServerReady {
  type: 'ready'
  data: {
    user_id: string
    ws_session_id: string
    conversations: Conversation[]
  }
}

export interface WSServerChatDelta {
  type: 'chat.delta'
  conversation_id: string
  data: { text: string }
}

export interface WSServerChatToolStart {
  type: 'chat.tool_start'
  conversation_id: string
  data: { tool_call_id: string; name: string; args: Record<string, unknown> }
}

export interface WSServerChatToolEnd {
  type: 'chat.tool_end'
  conversation_id: string
  data: { tool_call_id: string; result: string }
}

export interface WSServerChatDone {
  type: 'chat.done'
  conversation_id: string
  data: {
    final_message: Message | string
    aborted?: boolean
    usage?: { input?: number; output?: number; cache_read?: number }
    model?: string
  }
}

export interface WSServerChatQueued {
  type: 'chat.queued'
  conversation_id: string
  data: { position: number }
}

export interface WSServerToolApproveRequest {
  type: 'tool.approve_request'
  conversation_id: string
  data: {
    tool_call_id: string
    tool_name: string
    args: Record<string, unknown>
    reason: string
  }
}

export interface WSServerPing {
  type: 'ping'
}

export interface WSServerError {
  type: 'error'
  data: { message: string }
}

export type WSServerMessage =
  | WSServerHello
  | WSServerReady
  | WSServerChatDelta
  | WSServerChatToolStart
  | WSServerChatToolEnd
  | WSServerChatDone
  | WSServerChatQueued
  | WSServerToolApproveRequest
  | WSServerPing
  | WSServerError

/* ─── Auth ─── */

export interface AuthTokenResponse {
  token: string
}

/* ─── Theme ─── */

export type Theme = 'dark' | 'light'

/* ─── Tool Approval ─── */

export interface PendingApproval {
  conversationId: string
  toolCallId: string
  toolName: string
  args: Record<string, unknown>
  reason: string
}
