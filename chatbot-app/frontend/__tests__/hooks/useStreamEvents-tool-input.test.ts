import { useRef, useState } from 'react'
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useStreamEvents } from '@/hooks/useStreamEvents'
import type { Message, ToolExecution } from '@/types/chat'

vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: vi.fn(),
}))

function setup() {
  return renderHook(() => {
    const [messages, setMessages] = useState<Message[]>([])
    const [sessionState, setSessionState] = useState<any>({
      toolExecutions: [],
      interrupt: null,
      pendingOAuth: null,
    })
    const [uiState, setUIState] = useState<any>({
      agentStatus: 'thinking',
      isTyping: true,
      turnPhase: 'waiting_for_model',
      latencyMetrics: {},
    })
    const currentToolExecutionsRef = useRef<ToolExecution[]>([])
    const currentTurnIdRef = useRef<string | null>('turn-1')
    const startPollingRef = useRef<((sessionId: string) => void) | null>(null)
    const stopPollingRef = useRef<(() => void) | null>(null)

    const stream = useStreamEvents({
      sessionState,
      setSessionState,
      setMessages,
      setUIState,
      uiState,
      currentToolExecutionsRef,
      currentTurnIdRef,
      startPollingRef,
      stopPollingRef,
      sessionId: 'session-1',
    })

    return {
      ...stream,
      messages,
      sessionState,
      uiState,
      currentToolExecutionsRef,
    }
  })
}

describe('useStreamEvents tool input streaming', () => {
  it('renders the tool at start and fills its input as deltas arrive', () => {
    const hook = setup()

    act(() => {
      hook.result.current.handleStreamEvent({
        type: 'TOOL_CALL_START',
        toolCallId: 'tool-1',
        toolCallName: 'skill_executor',
      } as any)
    })

    expect(hook.result.current.messages[0].toolExecutions?.[0]).toMatchObject({
      id: 'tool-1',
      toolName: 'skill_executor',
      toolInputRaw: '',
      toolInputState: 'streaming',
    })
    expect(hook.result.current.uiState).toMatchObject({
      turnPhase: 'preparing_tool',
    })

    act(() => {
      hook.result.current.handleStreamEvent({
        type: 'TOOL_CALL_ARGS',
        toolCallId: 'tool-1',
        delta: '{"query":',
      } as any)
    })

    expect(hook.result.current.messages[0].toolExecutions?.[0]).toMatchObject({
      toolInputRaw: '{"query":',
      toolInputState: 'streaming',
    })

    act(() => {
      hook.result.current.handleStreamEvent({
        type: 'CUSTOM',
        name: 'tool_call_name_update',
        value: {
          toolCallId: 'tool-1',
          toolCallName: 'tavily_search',
        },
      } as any)
      hook.result.current.handleStreamEvent({
        type: 'TOOL_CALL_ARGS',
        toolCallId: 'tool-1',
        delta: '"mailbox"}',
      } as any)
      hook.result.current.handleStreamEvent({
        type: 'TOOL_CALL_END',
        toolCallId: 'tool-1',
      } as any)
    })

    expect(hook.result.current.messages[0].toolExecutions?.[0]).toMatchObject({
      toolName: 'tavily_search',
      toolInput: { query: 'mailbox' },
      toolInputRaw: '{"query":"mailbox"}',
      toolInputState: 'complete',
    })
    expect(hook.result.current.currentToolExecutionsRef.current[0]).toMatchObject({
      toolName: 'tavily_search',
      toolInput: { query: 'mailbox' },
      toolInputState: 'complete',
    })
    expect(hook.result.current.uiState).toMatchObject({
      turnPhase: 'running_tool',
    })

    act(() => {
      hook.result.current.handleStreamEvent({
        type: 'TOOL_CALL_RESULT',
        toolCallId: 'tool-1',
        content: JSON.stringify({ result: 'done' }),
      } as any)
    })

    expect(hook.result.current.uiState).toMatchObject({
      agentStatus: 'thinking',
      turnPhase: 'processing_tool_result',
    })
  })
})

describe('useStreamEvents replay deduplication', () => {
  it('consumes each buffered text event once for the same execution', () => {
    const hook = setup()
    const executionId = 'session-1:run-1'
    const events = [
      {
        type: 'RUN_STARTED',
        threadId: 'session-1',
        runId: 'run-1',
        _eventId: 1,
        _executionId: executionId,
      },
      {
        type: 'TEXT_MESSAGE_START',
        messageId: 'message-1',
        role: 'assistant',
        _eventId: 2,
        _executionId: executionId,
      },
      {
        type: 'TEXT_MESSAGE_CONTENT',
        messageId: 'message-1',
        delta: 'Hello',
        _eventId: 3,
        _executionId: executionId,
      },
      {
        type: 'TEXT_MESSAGE_END',
        messageId: 'message-1',
        _eventId: 4,
        _executionId: executionId,
      },
    ]

    act(() => {
      for (const event of events) {
        hook.result.current.handleStreamEvent(event as any)
      }
      for (const replayedEvent of events) {
        hook.result.current.handleStreamEvent(replayedEvent as any)
      }
    })

    expect(hook.result.current.messages).toHaveLength(1)
    expect(hook.result.current.messages[0]).toMatchObject({
      id: 'message-1',
      text: 'Hello',
      isStreaming: false,
    })
  })
})
