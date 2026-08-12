import type { ToolExecution } from '@/types/chat';
import {
  type RunStartedEvent,
  type RunFinishedEvent,
  type RunErrorEvent,
  type TextMessageStartEvent,
  type TextMessageContentEvent,
  type TextMessageEndEvent,
  type ToolCallStartEvent,
  type ToolCallArgsEvent,
  type ToolCallEndEvent,
  type ToolCallResultEvent,
  type ReasoningStartEvent,
  type ReasoningMessageStartEvent,
  type ReasoningMessageContentEvent,
  type ReasoningMessageEndEvent,
  type ReasoningEndEvent,
  type CustomEvent,
  EventType,
} from '@ag-ui/core';

export interface WorkspaceFile {
  filename: string;
  size_kb: string;
  last_modified: string;
  s3_key: string;
  tool_type: string;
}

// Discriminated union for all custom AG-UI event payloads, using `name` as discriminator.
export type CustomEventPayload =
  | { name: 'reasoning'; text: string; step: 'thinking' }
  | {
      name: 'interrupt';
      interrupts: Array<{
        id: string;
        name: string;
        reason?: { tool_name?: string; plan?: string; plan_preview?: string };
      }>;
    }
  | { name: 'warning'; message: string }
  | { name: 'browser_progress'; content: string; stepNumber: number }
  | { name: 'research_progress'; content: string; stepNumber: number }
  | { name: 'code_step'; stepNumber: number; content: string }
  | {
      name: 'code_todo_update';
      todos: Array<{ id: string; content: string; status: string; priority?: string }>;
    }
  | {
      name: 'code_result_meta';
      files_changed: string[];
      todos: any[];
      steps: number;
      status: string;
    }
  | { name: 'oauth_elicitation'; authUrl: string; message: string; elicitationId: string }
  | { name: 'swarm_node_start'; node_id: string; node_description: string }
  | { name: 'swarm_node_stop'; node_id: string; status: string }
  | {
      name: 'swarm_handoff';
      from_node: string;
      to_node: string;
      message?: string;
      context?: Record<string, any>;
    }
  | {
      name: 'swarm_complete';
      total_nodes: number;
      node_history: string[];
      status: string;
      final_response?: string;
      final_node_id?: string;
      shared_context?: Record<string, any>;
    }
  | {
      name: 'metadata';
      metadata?: { browserSessionId?: string; browserId?: string; [key: string]: any };
    };

// All AG-UI event type enum values used by this application.
// Used by useChatAPI whitelist and sseParser validation.
export const AGUI_EVENT_TYPES = [
  EventType.RUN_STARTED,
  EventType.RUN_FINISHED,
  EventType.RUN_ERROR,
  EventType.TEXT_MESSAGE_START,
  EventType.TEXT_MESSAGE_CONTENT,
  EventType.TEXT_MESSAGE_END,
  EventType.TOOL_CALL_START,
  EventType.TOOL_CALL_ARGS,
  EventType.TOOL_CALL_END,
  EventType.TOOL_CALL_RESULT,
  EventType.REASONING_START,
  EventType.REASONING_MESSAGE_START,
  EventType.REASONING_MESSAGE_CONTENT,
  EventType.REASONING_MESSAGE_END,
  EventType.REASONING_END,
  EventType.CUSTOM,
] as const;

export type AGUIEventType = (typeof AGUI_EVENT_TYPES)[number];

// Union of all AG-UI event types used by this application.
export type AGUIStreamEvent =
  | RunStartedEvent
  | RunFinishedEvent
  | RunErrorEvent
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
  | CustomEvent;

// Swarm mode types

export type SwarmState = 'idle' | 'running' | 'completed' | 'failed';

export interface SwarmAgentStep {
  nodeId: string;
  displayName: string;
  description?: string;
  startTime: number;
  endTime?: number;
  toolCalls?: Array<{
    toolName: string;
    status: 'running' | 'completed' | 'failed';
    toolUseId?: string;
  }>;
  status: 'running' | 'completed' | 'failed';
  responseText?: string;
  reasoningText?: string;
  handoffMessage?: string;
  handoffContext?: Record<string, any>;
}

export interface SwarmProgress {
  isActive: boolean;
  currentNode: string;
  currentNodeDescription: string;
  nodeHistory: string[];
  status: SwarmState;
  currentAction?: string;
  agentSteps?: SwarmAgentStep[];
}

export const SWARM_AGENT_DISPLAY_NAMES: Record<string, string> = {
  coordinator: 'Coordinator',
  web_researcher: 'Web Researcher',
  academic_researcher: 'Academic Researcher',
  word_agent: 'Word',
  excel_agent: 'Excel',
  powerpoint_agent: 'PowerPoint',
  data_analyst: 'Analyst',
  browser_agent: 'Browser',
  weather_agent: 'Weather',
  finance_agent: 'Finance',
  maps_agent: 'Maps',
  responder: 'Responder',
};

// Chat state interfaces

export interface ReasoningState {
  text: string;
  isActive: boolean;
}

export interface StreamingState {
  text: string;
  id: number;
}

export interface InterruptState {
  interrupts: Array<{
    id: string;
    name: string;
    reason?: {
      tool_name?: string;
      plan?: string;
      plan_preview?: string;
    };
  }>;
}

export interface PendingOAuthState {
  toolUseId?: string;
  toolName?: string;
  authUrl: string;
  serviceName: string;
  popupOpened: boolean;
  elicitationId?: string;
}

export interface ChatSessionState {
  reasoning: ReasoningState | null;
  streaming: StreamingState | null;
  toolExecutions: ToolExecution[];
  browserSession: {
    sessionId: string | null;
    browserId: string | null;
  } | null;
  browserProgress?: Array<{
    stepNumber: number;
    content: string;
  }>;
  researchProgress?: {
    stepNumber: number;
    content: string;
  };
  codeProgress?: Array<{
    stepNumber: number;
    content: string;
  }>;
  interrupt: InterruptState | null;
  swarmProgress?: SwarmProgress;
  pendingOAuth?: PendingOAuthState | null;
}

export type AgentStatus =
  | 'idle'
  | 'thinking'
  | 'responding'
  | 'researching'
  | 'compacting'
  | 'stopping'
  | 'swarm'
  | 'voice_connecting'
  | 'voice_connected'
  | 'voice_listening'
  | 'voice_processing'
  | 'voice_speaking';

export type TurnPhase =
  | 'idle'
  | 'submitting'
  | 'waiting_for_model'
  | 'reasoning'
  | 'preparing_tool'
  | 'running_tool'
  | 'processing_tool_result'
  | 'streaming_response'
  | 'waiting_for_user'
  | 'reconnecting';

export interface LatencyMetrics {
  requestStartTime: number | null;
  timeToFirstToken: number | null;
  endToEndLatency: number | null;
}

export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  cacheReadInputTokens?: number;
  cacheWriteInputTokens?: number;
}

export interface ChatUIState {
  isConnected: boolean;
  isTyping: boolean;
  isReconnecting?: boolean;
  reconnectAttempt?: number;
  showProgressPanel: boolean;
  agentStatus: AgentStatus;
  turnPhase: TurnPhase;
  latencyMetrics: LatencyMetrics;
}

// Re-export for convenience
export type { ToolExecution } from '@/types/chat';
