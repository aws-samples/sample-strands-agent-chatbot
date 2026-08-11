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
