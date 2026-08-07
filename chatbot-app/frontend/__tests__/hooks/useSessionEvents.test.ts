import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useSessionEvents } from '@/hooks/useSessionEvents'

vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: vi.fn().mockResolvedValue({
    tokens: {
      accessToken: {
        toString: () => 'session-event-token',
      },
    },
  }),
}))

function response(events: unknown[]) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ events }),
  } as Response)
}

async function flushAsyncWork() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
  })
}

async function advance(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms)
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
  })
}

const completion = {
  schemaVersion: 1,
  eventId: 'research-result:job-1:assistant',
  eventType: 'assistant.turn.completed',
  sessionId: 'session-1',
  userId: 'user-1',
  createdAt: '2026-08-06T00:00:02Z',
  originEventId: 'research-result:job-1',
  correlation: { jobId: 'job-1' },
  payload: {
    executionId: 'session-1:research-delivery-job-1',
  },
}

describe('useSessionEvents', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('baselines existing projections and emits only newly discovered events', async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response([]))
      .mockImplementationOnce(() => response([completion]))
    vi.stubGlobal('fetch', fetchMock)

    const hook = renderHook(() => useSessionEvents('session-1', true))
    await flushAsyncWork()
    expect(hook.result.current.events).toEqual([])

    await advance(2000)

    expect(hook.result.current.events).toEqual([completion])
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/session/events?session_id=session-1',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer session-event-token',
        }),
      }),
    )
  })

  it('surfaces initial projections so the consumer can close the history race', async () => {
    vi.stubGlobal('fetch', vi.fn(() => response([completion])))

    const hook = renderHook(() => useSessionEvents('session-1', true))
    await flushAsyncWork()

    expect(hook.result.current.events).toEqual([completion])
  })

  it('removes a projection after truncate deletes it from durable storage', async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response([completion]))
      .mockImplementationOnce(() => response([]))
    vi.stubGlobal('fetch', fetchMock)

    const hook = renderHook(() => useSessionEvents('session-1', true))
    await flushAsyncWork()
    expect(hook.result.current.events).toEqual([completion])

    await advance(2000)

    expect(hook.result.current.events).toEqual([])
  })

  it('hides the previous session projections immediately when switching sessions', async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response([completion]))
      .mockImplementationOnce(() => new Promise<Response>(() => {}))
    vi.stubGlobal('fetch', fetchMock)

    const hook = renderHook(
      ({ sessionId }) => useSessionEvents(sessionId),
      { initialProps: { sessionId: 'session-1' } },
    )
    await flushAsyncWork()
    expect(hook.result.current.events).toEqual([completion])

    hook.rerender({ sessionId: 'session-2' })

    expect(hook.result.current.events).toEqual([])
  })

  it('does not poll periodically without a pending delivery', async () => {
    const fetchMock = vi.fn(() => response([]))
    vi.stubGlobal('fetch', fetchMock)

    renderHook(() => useSessionEvents('session-1'))
    await flushAsyncWork()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await advance(60000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('does not refresh an idle session on window focus', async () => {
    const fetchMock = vi.fn(() => response([]))
    vi.stubGlobal('fetch', fetchMock)

    renderHook(() => useSessionEvents('session-1'))
    await flushAsyncWork()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    window.dispatchEvent(new Event('focus'))
    await flushAsyncWork()

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('polls immediately when pending delivery becomes active', async () => {
    const fetchMock = vi.fn(() => response([]))
    vi.stubGlobal('fetch', fetchMock)

    const hook = renderHook(
      ({ active }) => useSessionEvents('session-1', active),
      { initialProps: { active: false } },
    )
    await flushAsyncWork()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    hook.rerender({ active: true })
    await flushAsyncWork()
    expect(fetchMock).toHaveBeenCalledTimes(2)

    await advance(2000)
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('refreshes once when a delivery completes between job polls', async () => {
    const fetchMock = vi.fn(() => response([]))
    vi.stubGlobal('fetch', fetchMock)

    const hook = renderHook(
      ({ version }) => useSessionEvents('session-1', false, version),
      { initialProps: { version: 0 } },
    )
    await flushAsyncWork()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    hook.rerender({ version: 1 })
    await flushAsyncWork()
    expect(fetchMock).toHaveBeenCalledTimes(2)

    await advance(60000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('pauses while hidden and refreshes immediately when visible again', async () => {
    const fetchMock = vi.fn(() => response([]))
    vi.stubGlobal('fetch', fetchMock)

    renderHook(() => useSessionEvents('session-1'))
    await flushAsyncWork()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'hidden',
    })
    document.dispatchEvent(new Event('visibilitychange'))
    await advance(60000)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    })
    document.dispatchEvent(new Event('visibilitychange'))
    await flushAsyncWork()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
