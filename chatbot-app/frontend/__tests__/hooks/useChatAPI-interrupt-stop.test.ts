/**
 * A turn parked at an interrupt must remain stoppable.
 *
 * Regression: the backend yields the interrupt event and then lets the stream
 * close normally, so the client's completion path ran and cleared
 * activeRunIdRef. Research interrupts deliberately keep agentStatus non-idle to
 * avoid flicker, so the composer still showed a stop button — one that could
 * never work, logging only "No active run available to stop".
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useChatAPI } from '@/hooks/useChatAPI'

vi.mock('@/hooks/useSSEReconnect', () => ({
  useSSEReconnect: () => ({
    reset: vi.fn(),
    onStreamStart: vi.fn(),
    attemptReconnect: vi.fn(),
    isReconnecting: false,
    reconnectAttempt: 0,
  }),
}))

vi.mock('@/config/environment', () => ({
  getApiUrl: (path: string) => `http://localhost:3000/api/${path}`,
}))

vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: vi.fn().mockResolvedValue({
    tokens: { accessToken: { toString: () => 'test-token' } },
  }),
}))

/** Builds an SSE response body from the given event objects. */
function sseResponse(events: Array<Record<string, unknown>>) {
  const payload = events.map(e => `data: ${JSON.stringify(e)}\n\n`).join('')
  return {
    ok: true,
    status: 200,
    headers: { get: () => null },
    body: {
      getReader() {
        let sent = false
        return {
          read: async () => {
            if (sent) return { done: true, value: undefined }
            sent = true
            return { done: false, value: new TextEncoder().encode(payload) }
          },
          cancel: async () => {},
          releaseLock: () => {},
        }
      },
    },
  } as unknown as Response
}

const RUN_FINISHED = { type: 'RUN_FINISHED' }
const INTERRUPT = {
  type: 'CUSTOM',
  name: 'interrupt',
  value: {
    interrupts: [
      { id: 'int-1', name: 'chatbot-research-approval', reason: { tool_name: 'research_agent' } },
    ],
  },
}

function setup(handleStreamEvent = vi.fn()) {
  const stopFetch = vi.fn().mockResolvedValue({ ok: true, text: async () => '' })
  const setMessages = vi.fn()
  const hook = renderHook(() =>
    useChatAPI({
      backendUrl: 'http://localhost:8000',
      setUIState: vi.fn(),
      setMessages,
      handleStreamEvent,
      resetStreamingState: vi.fn(),
      sessionId: 'session-1',
      setSessionId: vi.fn(),
      currentModelId: 'us.anthropic.claude-opus-5',
      currentTemperature: 0.5,
    } as any),
  )
  return { hook, stopFetch, handleStreamEvent, setMessages }
}

/** Runs one turn whose stream ends with the given events. */
async function runTurn(
  hook: ReturnType<typeof setup>['hook'],
  events: Array<Record<string, unknown>>,
) {
  const fetchMock = vi.fn().mockResolvedValue(sseResponse(events))
  vi.stubGlobal('fetch', fetchMock)
  await act(async () => {
    await hook.result.current.sendMessage('hello')
  })
  return fetchMock
}

/** Attempts a stop and reports whether a stop request was actually issued. */
async function tryStop(hook: ReturnType<typeof setup>['hook']) {
  const stopFetch = vi.fn().mockResolvedValue({ ok: true, text: async () => '' })
  vi.stubGlobal('fetch', stopFetch)
  let accepted: boolean | undefined
  await act(async () => {
    accepted = await hook.result.current.sendStopSignal()
  })
  const calls = stopFetch.mock.calls.filter(c => String(c[0]).includes('stream/stop'))
  return { accepted, requested: calls.length > 0, body: calls[0]?.[1]?.body }
}

describe('useChatAPI — stopping a turn that parked at an interrupt', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
  })

  it('still has a run to stop after an interrupt ends the stream', async () => {
    const { hook } = setup()
    await runTurn(hook, [INTERRUPT])

    expect(hook.result.current.hasStoppableRun).toBe(true)
    const { requested, accepted } = await tryStop(hook)

    expect(requested).toBe(true)
    expect(accepted).toBe(true)
    expect(hook.result.current.hasStoppableRun).toBe(false)
  })

  it('targets the run that was interrupted', async () => {
    const { hook } = setup()
    await runTurn(hook, [INTERRUPT])

    const { body } = await tryStop(hook)
    const parsed = JSON.parse(String(body))

    // The hook owns session id resolution (it may restore or generate one), so
    // assert a run is targeted rather than pinning the session value.
    expect(typeof parsed.sessionId).toBe('string')
    expect(parsed.sessionId.length).toBeGreaterThan(0)
    expect(typeof parsed.runId).toBe('string')
    expect(parsed.runId.length).toBeGreaterThan(0)
  })

  // The complement: a genuinely finished turn must not stay stoppable, or a
  // later stop would target a run that is already over.
  it('has nothing to stop after a turn finishes normally', async () => {
    const { hook } = setup()
    await runTurn(hook, [RUN_FINISHED])

    expect(hook.result.current.hasStoppableRun).toBe(false)
    const { requested, accepted } = await tryStop(hook)

    expect(requested).toBe(false)
    expect(accepted).toBe(false)
  })

  it('clears the run once an interrupted turn is resumed to completion', async () => {
    const { hook } = setup()
    await runTurn(hook, [INTERRUPT])
    // Answering the approval sends another turn, which this time completes.
    await runTurn(hook, [RUN_FINISHED])

    const { requested } = await tryStop(hook)

    expect(requested).toBe(false)
  })

  it('sends Workspace attachment paths in AG-UI state', async () => {
    const { hook } = setup()
    const fetchMock = vi.fn().mockResolvedValue(sseResponse([RUN_FINISHED]))
    vi.stubGlobal('fetch', fetchMock)

    await act(async () => {
      await hook.result.current.sendMessage(
        'inspect this file',
        [],
        undefined,
        undefined,
        undefined,
        undefined,
        [{
          name: 'large.jsonl',
          type: 'application/x-ndjson',
          size: 5_000_000,
          path: 'uploads/large.jsonl',
        }],
      )
    })

    const chatCall = fetchMock.mock.calls.find(call =>
      String(call[0]).includes('stream/chat'),
    )
    expect(chatCall).toBeDefined()
    const body = JSON.parse(String(chatCall?.[1]?.body))
    expect(body.state.workspace_paths).toEqual(['uploads/large.jsonl'])
  })
})

describe('useChatAPI — background execution replay', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    vi.mocked(sessionStorage.getItem).mockImplementation(key =>
      key === 'chat-session-id' ? 'session-1' : null,
    )
  })

  it('replays a buffered completion with auth through the normal event handler', async () => {
    let finishCleanup: (() => void) | undefined
    const cleanup = new Promise<void>(resolve => {
      finishCleanup = resolve
    })
    const handleStreamEvent = vi.fn().mockImplementation(async event => {
      if (event.type === 'RUN_FINISHED') await cleanup
    })
    const { hook, setMessages } = setup(handleStreamEvent)
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    const activeSessionId = sessionStorage.getItem('chat-session-id')
    expect(activeSessionId).toBeTruthy()

    const events = [
      { type: 'RUN_STARTED', threadId: activeSessionId, runId: 'delivery-1' },
      { type: 'TEXT_MESSAGE_START', messageId: 'completion-1', role: 'assistant' },
      { type: 'TEXT_MESSAGE_CONTENT', messageId: 'completion-1', delta: 'Research is ready.' },
      { type: 'TEXT_MESSAGE_END', messageId: 'completion-1' },
      { type: 'RUN_FINISHED', threadId: activeSessionId, runId: 'delivery-1' },
    ]
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(events))
    vi.stubGlobal('fetch', fetchMock)

    const executionId = `${activeSessionId}:research-delivery-job-1`
    let replayed = false
    let replayPromise: Promise<boolean> | undefined
    await act(async () => {
      replayPromise = hook.result.current.replayExecution(executionId, {
        logicalMessageId: 'mailbox:research-result:job-1:1',
      })
      await Promise.resolve()
    })
    expect(replayed).toBe(false)
    expect(hook.result.current.hasStoppableRun).toBe(false)

    await act(async () => {
      finishCleanup?.()
      replayed = await replayPromise!
    })

    expect(replayed).toBe(true)
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(
        `executionId=${encodeURIComponent(executionId)}`,
      ),
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer test-token',
          Accept: 'text/event-stream',
        }),
      }),
    )
    expect(handleStreamEvent.mock.calls.map(([event]) => event.type)).toEqual(
      events.map(event => event.type),
    )
    const identityUpdate =
      setMessages.mock.calls[setMessages.mock.calls.length - 1]?.[0]
    expect(identityUpdate([
      {
        id: 'completion-1',
        text: 'Research is ready.',
        timestamp: '2026-08-06T00:00:00Z',
      },
    ])).toEqual([
      expect.objectContaining({
        id: 'mailbox:research-result:job-1:1',
        logicalMessageId: 'mailbox:research-result:job-1:1',
      }),
    ])
  })
})
