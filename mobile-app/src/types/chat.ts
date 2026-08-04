import type { ImageData, TokenUsage, TodoItem, InterruptData } from './events'

export type AgentStatus = 'idle' | 'thinking' | 'responding'

export interface ProgressStep {
  stepNumber: number
  content: string
}

export interface SwarmAgentStep {
  nodeId: string
  description: string
  status: 'running' | 'completed' | 'failed'
  handoffTo?: string
}

export interface ToolExecution {
  id: string
  toolName: string
  toolInput: string // accumulated JSON arg string
  toolResult?: string
  images?: ImageData[]
  metadata?: Record<string, unknown>
  resultStatus?: string
  isComplete: boolean
  isExpanded: boolean
  codeSteps: Array<{ stepNumber: number; content: string }>
  codeTodos: TodoItem[]
  codeResultMeta?: {
    files_changed: string[]
    todos: TodoItem[]
    steps: number
    status: 'completed' | 'failed'
  }
}

export interface UploadedFile {
  name: string
  type: string // MIME type
}

export interface PickedDocument {
  name: string
  mimeType: string
  base64: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'error' | 'warning'
  text: string
  isStreaming: boolean
  toolExecutions: ToolExecution[]
  reasoningText: string
  isReasoningExpanded: boolean
  images?: ImageData[]
  uploadedFiles?: UploadedFile[]
  tokenUsage?: TokenUsage
  browserProgress: ProgressStep[]
  researchProgress: ProgressStep[]
  swarmAgentSteps: SwarmAgentStep[]
  swarmCompleted: boolean
  timestamp: number
}

export interface SessionMeta {
  sessionId: string
  title: string
  messageCount: number
  lastMessageAt: string
  status: string
  starred?: boolean
  tags?: string[]
}

export interface PendingOAuth {
  authUrl: string
  message: string
  elicitationId: string
}

export interface PendingInterrupt {
  interrupts: InterruptData[]
}

export interface ChatState {
  sessionId: string | null
  messages: Message[]
  agentStatus: AgentStatus
  thinkingMessage: string
  pendingOAuth: PendingOAuth | null
  pendingInterrupt: PendingInterrupt | null
  isReconnecting?: boolean
  reconnectAttempt?: number
}

export interface RunAgentInput {
  threadId: string
  runId: string
  messages: Array<{ id: string; role: string; content: string }>
  tools: unknown[]
  context: unknown[]
  state: {
    model_id: string
    temperature: number
    request_type: 'text' | 'skill'
    system_prompt: string
    selected_artifact_id: null
    enabled_tools: string[]
    /** false opts out of 3LO skills, which need an OAuth callback page. */
    allow_user_federation?: boolean
  }
}

export function makeEmptyMessage(id: string, role: Message['role']): Message {
  return {
    id,
    role,
    text: '',
    isStreaming: false,
    toolExecutions: [],
    reasoningText: '',
    isReasoningExpanded: false,
    browserProgress: [],
    researchProgress: [],
    swarmAgentSteps: [],
    swarmCompleted: false,
    timestamp: Date.now(),
  }
}
