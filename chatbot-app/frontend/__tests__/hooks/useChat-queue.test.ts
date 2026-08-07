/**
 * Wiring tests for the message queue inside useChat.
 *
 * useMessageQueue is unit-tested separately; what matters here is that useChat
 * hands it the right signals: a queued message is only flushed when a turn
 * really finished, and the interrupt/OAuth state is read after the finishing
 * turn's events have been committed rather than from a stale snapshot.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useChat } from '@/hooks/useChat'

vi.mock('@/utils/chat', () => ({
  detectBackendUrl: vi.fn().mockResolvedValue({ url: 'http://localhost:8000', connected: true }),
  getToolIconById: vi.fn(),
  getCategoryColor: vi.fn(),
}))

// Lets a test raise an interrupt/OAuth "as of" the moment a turn finishes.
const streamState: { interrupt: unknown; pendingOAuth: unknown } = {
  interrupt: null,
  pendingOAuth: null,
}

vi.mock('@/hooks/useStreamEvents', () => ({
  useStreamEvents: vi.fn(({ setSessionState }: any) => ({
    handleStreamEvent: vi.fn(),
    resetStreamingState: vi.fn(),
    // Test seam: apply whatever the "backend" reported for this turn.
    __applyStreamState: () => setSessionState((prev: any) => ({ ...prev, ...streamState })),
  })),
}))

type SendArgs = [string, File[] | undefined, (() => void)?, ((e: string) => void)?, string?, (string | null)?]
const sendCalls: SendArgs[] = []
let failNextSend = false

const apiSendMessage = vi.fn(async (...args: SendArgs) => {
  sendCalls.push(args)
  const [, , onSuccess, onError] = args
  // Mirror the real hook: stream events land before the completion callback.
  const { useStreamEvents } = await import('@/hooks/useStreamEvents')
  const instance = (useStreamEvents as any).mock.results.at(-1)?.value
  instance?.__applyStreamState?.()
  if (failNextSend) {
    failNextSend = false
    onError?.('boom')
  } else {
    onSuccess?.()
  }
})

const sendStopSignal = vi.fn().mockResolvedValue(true)
const apiReplayExecution = vi.fn().mockResolvedValue(true)

vi.mock('@/hooks/useChatAPI', () => ({
  useChatAPI: vi.fn(() => ({
    newChat: vi.fn().mockResolvedValue(true),
    compactSession: vi.fn(),
    truncateSession: vi.fn(),
    summarizeForCompact: vi.fn(),
    listSessionEvents: vi.fn(),
    sendMessage: apiSendMessage,
    replayExecution: apiReplayExecution,
    cleanup: vi.fn(),
    sendStopSignal,
    loadSession: vi.fn().mockResolvedValue({ preferences: null, messages: [] }),
    isReconnecting: false,
    reconnectAttempt: 0,
  })),
}))

vi.mock('@/config/environment', () => ({
  getApiUrl: vi.fn((path: string) => `http://localhost:8000/${path}`),
}))

vi.mock('@/lib/api-client', () => ({
  apiPost: vi.fn().mockResolvedValue({ success: true }),
  apiGet: vi.fn().mockResolvedValue({ models: [] }),
}))

vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: vi.fn().mockResolvedValue({ tokens: null }),
}))

async function mount() {
  const hook = renderHook(() => useChat())
  await act(async () => { await Promise.resolve() })
  return hook
}

/** Text of every message actually dispatched to the backend. */
const sentTexts = () => sendCalls.map(c => c[0])

describe('useChat message queue wiring', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sendCalls.length = 0
    failNextSend = false
    streamState.interrupt = null
    streamState.pendingOAuth = null
    sessionStorage.clear()
  })

  it('starts with an empty queue', async () => {
    const { result } = await mount()
    expect(result.current.queuedMessages).toEqual([])
    expect(result.current.queueHoldReason).toBeNull()
  })

  it('queues a message without sending it', async () => {
    const { result } = await mount()

    act(() => { result.current.enqueueMessage('later') })

    expect(result.current.queuedMessages.map(m => m.text)).toEqual(['later'])
    expect(sentTexts()).toEqual([])
  })

  it('flushes the queue after a turn finishes normally', async () => {
    const { result } = await mount()

    act(() => { result.current.enqueueMessage('follow-up') })
    await act(async () => { await result.current.sendMessage('first') })

    await waitFor(() => expect(sentTexts()).toEqual(['first', 'follow-up']))
    expect(result.current.queuedMessages).toEqual([])
  })

  it('flushes a user turn queued while a research delivery renders', async () => {
    const { result } = await mount()

    act(() => { result.current.enqueueMessage('question during delivery') })
    await act(async () => {
      await result.current.replayExecution('session-1:research-delivery-job-1')
    })

    expect(apiReplayExecution).toHaveBeenCalledWith(
      'session-1:research-delivery-job-1',
    )
    expect(sentTexts()).toEqual(['question during delivery'])
    expect(result.current.queuedMessages).toEqual([])
  })

  // The regression this whole design exists for: an interrupted turn closes its
  // stream normally, so the success callback fires while the run is parked with
  // a toolUse awaiting its toolResult. Sending there corrupts the history.
  it('holds instead of flushing when the turn ended at a tool approval', async () => {
    const { result } = await mount()
    streamState.interrupt = { interrupts: [{ id: 'i1', name: 'approve' }] }

    act(() => { result.current.enqueueMessage('follow-up') })
    await act(async () => { await result.current.sendMessage('first') })

    await waitFor(() => expect(result.current.queueHoldReason).toBe('interrupt'))
    expect(sentTexts()).toEqual(['first'])
    expect(result.current.queuedMessages.map(m => m.text)).toEqual(['follow-up'])
  })

  it('holds instead of flushing while an OAuth elicitation is pending', async () => {
    const { result } = await mount()
    streamState.pendingOAuth = { authUrl: 'https://example.test', serviceName: 'GitHub' }

    act(() => { result.current.enqueueMessage('follow-up') })
    await act(async () => { await result.current.sendMessage('first') })

    await waitFor(() => expect(result.current.queueHoldReason).toBe('oauth'))
    expect(sentTexts()).toEqual(['first'])
  })

  it('holds when a turn ends in an error', async () => {
    const { result } = await mount()
    failNextSend = true

    act(() => { result.current.enqueueMessage('follow-up') })
    await act(async () => { await result.current.sendMessage('first') })

    await waitFor(() => expect(result.current.queueHoldReason).toBe('error'))
    expect(sentTexts()).toEqual(['first'])
  })

  it('holds after the user stops a turn', async () => {
    const { result } = await mount()

    act(() => { result.current.enqueueMessage('follow-up') })
    await act(async () => { await result.current.stopGeneration() })

    await waitFor(() => expect(result.current.queueHoldReason).toBe('stopped'))
    expect(sentTexts()).toEqual([])
  })

  it('stops before sending the selected queued message', async () => {
    const { result } = await mount()

    act(() => { result.current.enqueueMessage('first queued') })
    act(() => { result.current.enqueueMessage('send this now') })
    const selectedId = result.current.queuedMessages[1].id

    let interrupted = false
    await act(async () => {
      interrupted = await result.current.interruptWithQueuedMessage(selectedId)
    })

    expect(interrupted).toBe(true)
    expect(sendStopSignal).toHaveBeenCalledTimes(1)
    expect(sendStopSignal.mock.invocationCallOrder[0])
      .toBeLessThan(apiSendMessage.mock.invocationCallOrder[0])
    expect(sentTexts()[0]).toBe('send this now')
    await waitFor(() => {
      expect(sentTexts()).toEqual(['send this now', 'first queued'])
    })
    expect(result.current.queuedMessages).toEqual([])
  })

  it('keeps the selected message queued when the stop request fails', async () => {
    sendStopSignal.mockResolvedValueOnce(false)
    const { result } = await mount()

    act(() => { result.current.enqueueMessage('first queued') })
    act(() => { result.current.enqueueMessage('selected') })
    const selectedId = result.current.queuedMessages[1].id

    let interrupted = true
    await act(async () => {
      interrupted = await result.current.interruptWithQueuedMessage(selectedId)
    })

    expect(interrupted).toBe(false)
    expect(sentTexts()).toEqual([])
    expect(result.current.queuedMessages.map(m => m.text)).toEqual([
      'selected',
      'first queued',
    ])
    expect(result.current.queueHoldReason).toBeNull()
  })

  it('sends a selected queued message immediately while idle', async () => {
    const { result } = await mount()

    act(() => { result.current.enqueueMessage('first queued') })
    act(() => { result.current.enqueueMessage('send this now') })
    const selectedId = result.current.queuedMessages[1].id

    let sent = false
    await act(async () => {
      sent = await result.current.sendQueuedMessageNow(selectedId)
    })

    expect(sent).toBe(true)
    expect(sendStopSignal).not.toHaveBeenCalled()
    expect(sentTexts()[0]).toBe('send this now')
    await waitFor(() => {
      expect(sentTexts()).toEqual(['send this now', 'first queued'])
    })
    expect(result.current.queuedMessages).toEqual([])
  })

  it('does not send the selected message after a session switch', async () => {
    let acceptStop: (accepted: boolean) => void = () => {}
    sendStopSignal.mockReturnValueOnce(
      new Promise<boolean>(resolve => { acceptStop = resolve }),
    )
    const { result } = await mount()

    act(() => { result.current.enqueueMessage('selected') })
    const selectedId = result.current.queuedMessages[0].id

    let interruptPromise: Promise<boolean>
    act(() => {
      interruptPromise = result.current.interruptWithQueuedMessage(selectedId)
    })
    await act(async () => {
      await result.current.loadSession('different-session')
      acceptStop(true)
    })

    await expect(interruptPromise!).resolves.toBe(false)
    expect(sentTexts()).toEqual([])
  })

  it('sends the held message once the user confirms', async () => {
    const { result } = await mount()

    act(() => { result.current.enqueueMessage('follow-up') })
    await act(async () => { await result.current.stopGeneration() })
    await waitFor(() => expect(result.current.queueHoldReason).toBe('stopped'))

    await act(async () => { result.current.releaseQueue() })

    await waitFor(() => expect(sentTexts()).toEqual(['follow-up']))
    expect(result.current.queueHoldReason).toBeNull()
  })

  it('discards the queue on request', async () => {
    const { result } = await mount()

    act(() => { result.current.enqueueMessage('a') })
    act(() => { result.current.enqueueMessage('b') })
    act(() => { result.current.clearQueuedMessages() })

    expect(result.current.queuedMessages).toEqual([])
    expect(sentTexts()).toEqual([])
  })

  it('removes a single queued message', async () => {
    const { result } = await mount()

    act(() => { result.current.enqueueMessage('a') })
    act(() => { result.current.enqueueMessage('b') })
    act(() => { result.current.removeQueuedMessage(result.current.queuedMessages[0].id) })

    expect(result.current.queuedMessages.map(m => m.text)).toEqual(['b'])
  })

  // Approving a research resumes the SAME turn. That resumed turn is what
  // finishes the work the queue is waiting on, so it must settle the turn too —
  // otherwise the queued message is held by a turn that never reports an outcome.
  it('flushes the queue after an approved turn completes', async () => {
    const { result } = await mount()
    streamState.interrupt = { interrupts: [{ id: 'i1', name: 'approve', reason: { tool_name: 'research_agent' } }] }

    act(() => { result.current.enqueueMessage('follow-up') })
    await act(async () => { await result.current.sendMessage('research this') })
    await waitFor(() => expect(result.current.queueHoldReason).toBe('interrupt'))

    // User approves; the run resumes and this time finishes cleanly.
    streamState.interrupt = null
    await act(async () => { await result.current.respondToInterrupt('i1', 'yes') })

    await waitFor(() => expect(sentTexts()).toContain('follow-up'))
    expect(result.current.queuedMessages).toEqual([])
  })

  it('holds again when the resumed turn errors', async () => {
    const { result } = await mount()
    streamState.interrupt = { interrupts: [{ id: 'i1', name: 'approve', reason: { tool_name: 'research_agent' } }] }

    act(() => { result.current.enqueueMessage('follow-up') })
    await act(async () => { await result.current.sendMessage('research this') })
    await waitFor(() => expect(result.current.queueHoldReason).toBe('interrupt'))

    streamState.interrupt = null
    failNextSend = true
    await act(async () => { await result.current.respondToInterrupt('i1', 'yes') })

    await waitFor(() => expect(result.current.queueHoldReason).toBe('error'))
    expect(sentTexts()).not.toContain('follow-up')
  })

  it('forwards the artifact context captured at enqueue time', async () => {
    const { result } = await mount()

    act(() => { result.current.enqueueMessage('q', [], 'ctx', 'artifact-1') })
    await act(async () => { await result.current.sendMessage('first') })

    await waitFor(() => expect(sendCalls).toHaveLength(2))
    const flushed = sendCalls[1]
    expect(flushed[0]).toBe('q')
    expect(flushed[4]).toBe('ctx')
    expect(flushed[5]).toBe('artifact-1')
  })
})
