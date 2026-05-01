/* ─── Domain Models ─── */

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: number
  toolCalls?: ToolCallInfo[]
}

export interface ToolCallInfo {
  id: string
  name: string
  args: Record<string, unknown>
  result?: string
  status: 'running' | 'done' | 'error'
}

export interface Conversation {
  id: string
  title: string
  updatedAt: number
  active: boolean
}

export interface WorkerStatus {
  worker_id: string
  status: 'healthy' | 'stale' | 'dead'
  last_heartbeat: number
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

export interface WSClientSessionCreate {
  type: 'session.create'
}

export type WSClientMessage =
  | WSClientIdentify
  | WSClientChatSend
  | WSClientChatAbort
  | WSClientToolApprove
  | WSClientPong
  | WSClientSessionCreate

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
  data: { final_message: Message; aborted?: boolean }
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

export interface WSServerClusterStatus {
  type: 'cluster.status'
  data: { workers: WorkerStatus[] }
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
  | WSServerClusterStatus
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
