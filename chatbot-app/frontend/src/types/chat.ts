export interface ToolExecution {
  id: string
  toolName: string
  toolInput?: any
  reasoning: string[]
  reasoningText?: string
  toolResult?: string
  metadata?: Record<string, any>
  images?: Array<
    | { format: string; data: string }
    | { type: 'url'; url: string; thumbnail?: string }
  >
  isComplete: boolean
  isCancelled?: boolean
  isExpanded: boolean
  streamingResponse?: string
  // Code agent terminal log (persisted for reconnect + page refresh)
  codeSteps?: Array<{ stepNumber: number; content: string }>
  codeTodos?: Array<{ id: string; content: string; status: string; priority?: string }>
  codeResultMeta?: { files_changed: string[]; todos: any[]; steps: number }
}

export interface Message {
  id: string
  text: string
  sender: 'user' | 'bot'
  timestamp: string
  isStreaming?: boolean
  toolExecutions?: ToolExecution[]
  images?: Array<
    | { format: string; data: string }
    | { type: 'url'; url: string; thumbnail?: string }
  >
  documents?: Array<{
    filename: string
    tool_type: string  // 'word_document', 'powerpoint', etc.
  }>
  isToolMessage?: boolean // Mark messages that are purely for tool execution display
  turnId?: string // Turn ID for grouping messages by conversation turn
  startsNewAssistantTurn?: boolean // Internal boundary for synthetic/background turns
  toolUseId?: string // Tool use ID for session-based image paths
  uploadedFiles?: Array<{
    name: string
    type: string
    size: number
  }>
  latencyMetrics?: {
    timeToFirstToken?: number  // ms from request to first response
    endToEndLatency?: number   // ms from request to completion
  }
  tokenUsage?: {
    inputTokens: number
    outputTokens: number
    totalTokens: number
    cacheReadInputTokens?: number
    cacheWriteInputTokens?: number
  }
  feedback?: 'up' | 'down' | null
  // Swarm message markers (multi-agent)
  isSwarmNode?: boolean
  swarmNodeId?: string
  swarmNodeDescription?: string
  // Swarm context for history (parsed from <swarm_context> tag)
  swarmContext?: {
    agentsUsed: string[]
    sharedContext?: Record<string, any>
  }
  // Raw unix ms timestamp (for truncation)
  rawTimestamp?: number
  // Voice mode marker
  isVoiceMessage?: boolean
  // Artifact reference (for composer workflow results)
  artifactReference?: {
    id: string
    type: string
    title: string
    wordCount?: number
  }
}

export interface Tool {
  id: string
  name: string
  description: string
  icon: string
  enabled: boolean
  import_path: string
  category: string
  tool_type?: "local" | "builtin" | "gateway" | "runtime-a2a"
  connection_status?: "connected" | "disconnected" | "invalid" | "unknown"
}
