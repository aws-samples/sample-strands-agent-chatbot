// ─── Standard AG-UI event types (camelCase — matches BFF output) ─────────────

export interface RunStartedEvent {
  type: 'RUN_STARTED'
  threadId: string
  runId: string
}

export interface TextMessageStartEvent {
  type: 'TEXT_MESSAGE_START'
  messageId: string
  role: 'assistant'
}

export interface TextMessageContentEvent {
  type: 'TEXT_MESSAGE_CONTENT'
  messageId: string
  delta: string
}

export interface TextMessageEndEvent {
  type: 'TEXT_MESSAGE_END'
  messageId: string
}

export interface ToolCallStartEvent {
  type: 'TOOL_CALL_START'
  toolCallId: string
  toolCallName: string
  parentMessageId?: string | null
}

export interface ToolCallArgsEvent {
  type: 'TOOL_CALL_ARGS'
  toolCallId: string
  delta: string
}

export interface ToolCallEndEvent {
  type: 'TOOL_CALL_END'
  toolCallId: string
}

export interface ImageData {
  format?: string
  data?: string // base64
  type?: 'url'
  url?: string
  thumbnail?: string
  title?: string
}

export interface TokenUsage {
  inputTokens: number
  outputTokens: number
  totalTokens: number
  cacheReadInputTokens?: number
  cacheWriteInputTokens?: number
}

export interface ToolCallResultContent {
  result: string
  images?: ImageData[]
  status?: string
  metadata?: Record<string, unknown>
}

export interface ToolCallResultEvent {
  type: 'TOOL_CALL_RESULT'
  messageId: string
  toolCallId: string
  content: string // JSON-encoded ToolCallResultContent
}

export interface RunFinishedEvent {
  type: 'RUN_FINISHED'
  threadId: string
  runId: string
  outcome?: {
    type: 'success' | 'interrupt'
    interrupts?: Array<{
      id: string
      message?: string
      metadata?: {
        name?: string
        reason?: InterruptReason
      }
    }>
  }
}

export interface RunErrorEvent {
  type: 'RUN_ERROR'
  message: string
}

export interface ReasoningStartEvent {
  type: 'REASONING_START'
  messageId: string
}

export interface ReasoningMessageStartEvent {
  type: 'REASONING_MESSAGE_START'
  messageId: string
}

export interface ReasoningMessageContentEvent {
  type: 'REASONING_MESSAGE_CONTENT'
  messageId: string
  delta: string
}

export interface ReasoningMessageEndEvent {
  type: 'REASONING_MESSAGE_END'
  messageId: string
}

export interface ReasoningEndEvent {
  type: 'REASONING_END'
  messageId: string
}

// ─── CUSTOM event subtypes ────────────────────────────────────────────────────

export type CustomEventName =
  | 'thinking'
  | 'reasoning'
  | 'stream_stopped'
  | 'complete_metadata'
  | 'interrupt'
  | 'warning'
  | 'browser_progress'
  | 'research_progress'
  | 'code_step'
  | 'code_todo_update'
  | 'code_result_meta'
  | 'oauth_elicitation'
  | 'swarm_node_start'
  | 'swarm_node_stop'
  | 'swarm_handoff'
  | 'swarm_complete'
  | 'metadata'
  | 'execution_meta'
  | 'code_agent_started'
  | 'code_agent_heartbeat'

export interface CustomEvent<N extends string, V> {
  type: 'CUSTOM'
  name: N
  value: V
}

export type ThinkingEvent = CustomEvent<'thinking', { message: string }>
export type ReasoningEvent = CustomEvent<'reasoning', { text: string; step: string }>
export type StreamStoppedEvent = CustomEvent<'stream_stopped', { message: string }>
export type CompleteMetadataEvent = CustomEvent<'complete_metadata', {
  images?: ImageData[]
  usage?: TokenUsage
}>

export interface InterruptReason {
  tool_name?: string
  plan?: string
  plan_preview?: string
}

export interface InterruptData {
  id: string
  name: string
  reason?: InterruptReason
}

export type InterruptEvent = CustomEvent<'interrupt', { interrupts: InterruptData[] }>
export type WarningEvent = CustomEvent<'warning', { message: string }>
export type BrowserProgressEvent = CustomEvent<'browser_progress', { content: string; stepNumber: number }>
export type ResearchProgressEvent = CustomEvent<'research_progress', { content: string; stepNumber: number }>
export type CodeStepEvent = CustomEvent<'code_step', { content: string; stepNumber: number }>

export interface TodoItem {
  id: string
  content: string
  status: string
  priority?: string
}

export type CodeTodoUpdateEvent = CustomEvent<'code_todo_update', { todos: TodoItem[] }>
export type CodeResultMetaEvent = CustomEvent<'code_result_meta', {
  files_changed: string[]
  todos: TodoItem[]
  steps: number
  status: 'completed' | 'failed'
}>
export type OAuthElicitationEvent = CustomEvent<'oauth_elicitation', {
  authUrl: string
  message: string
  elicitationId: string
}>
export type SwarmNodeStartEvent = CustomEvent<'swarm_node_start', { node_id: string; node_description: string }>
export type SwarmNodeStopEvent = CustomEvent<'swarm_node_stop', { node_id: string; status: string }>
export type SwarmHandoffEvent = CustomEvent<'swarm_handoff', {
  from_node: string
  to_node: string
  message?: string
  context?: Record<string, unknown>
}>
export type SwarmCompleteEvent = CustomEvent<'swarm_complete', {
  total_nodes: number
  node_history: unknown[]
  status: string
  final_response?: string
}>
export type MetadataEvent = CustomEvent<'metadata', {
  metadata?: { browserSessionId?: string; browserId?: string }
}>
export type ExecutionMetaEvent = CustomEvent<'execution_meta', { executionId: string }>
export type CodeAgentStartedEvent = CustomEvent<'code_agent_started', {}>
export type CodeAgentHeartbeatEvent = CustomEvent<'code_agent_heartbeat', { elapsed_seconds: number }>

export type AnyCustomEvent =
  | ThinkingEvent
  | ReasoningEvent
  | StreamStoppedEvent
  | CompleteMetadataEvent
  | InterruptEvent
  | WarningEvent
  | BrowserProgressEvent
  | ResearchProgressEvent
  | CodeStepEvent
  | CodeTodoUpdateEvent
  | CodeResultMetaEvent
  | OAuthElicitationEvent
  | SwarmNodeStartEvent
  | SwarmNodeStopEvent
  | SwarmHandoffEvent
  | SwarmCompleteEvent
  | MetadataEvent
  | ExecutionMetaEvent
  | CodeAgentStartedEvent
  | CodeAgentHeartbeatEvent

export type AGUIEvent =
  | RunStartedEvent
  | TextMessageStartEvent
  | TextMessageContentEvent
  | TextMessageEndEvent
  | ToolCallStartEvent
  | ToolCallArgsEvent
  | ToolCallEndEvent
  | ToolCallResultEvent
  | ReasoningStartEvent
  | ReasoningMessageStartEvent
  | ReasoningMessageContentEvent
  | ReasoningMessageEndEvent
  | ReasoningEndEvent
  | RunFinishedEvent
  | RunErrorEvent
  | AnyCustomEvent

export const AGUI_STANDARD_TYPES = new Set([
  'RUN_STARTED', 'TEXT_MESSAGE_START', 'TEXT_MESSAGE_CONTENT', 'TEXT_MESSAGE_END',
  'TOOL_CALL_START', 'TOOL_CALL_ARGS', 'TOOL_CALL_END', 'TOOL_CALL_RESULT',
  'REASONING_START', 'REASONING_MESSAGE_START', 'REASONING_MESSAGE_CONTENT',
  'REASONING_MESSAGE_END', 'REASONING_END',
  'RUN_FINISHED', 'RUN_ERROR', 'CUSTOM',
])
