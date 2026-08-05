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

vi.mock('@/hooks/useChatAPI', () => ({
  useChatAPI: vi.fn(() => ({
    newChat: vi.fn().mockResolvedValue(true),
    compactSession: vi.fn(),
    truncateSession: vi.fn(),
    summarizeForCompact: vi.fn(),
    listSessionEvents: vi.fn(),
    sendMessage: apiSendMessage,
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
