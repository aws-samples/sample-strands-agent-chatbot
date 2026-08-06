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

    const hook = renderHook(() => useSessionEvents('session-1'))
    await flushAsyncWork()
    expect(hook.result.current.events).toEqual([])

    await act(async () => {
      vi.advanceTimersByTime(2000)
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

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

    const hook = renderHook(() => useSessionEvents('session-1'))
    await flushAsyncWork()

    expect(hook.result.current.events).toEqual([completion])
  })

  it('removes a projection after truncate deletes it from durable storage', async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response([completion]))
      .mockImplementationOnce(() => response([]))
    vi.stubGlobal('fetch', fetchMock)

    const hook = renderHook(() => useSessionEvents('session-1'))
    await flushAsyncWork()
    expect(hook.result.current.events).toEqual([completion])

    await act(async () => {
      vi.advanceTimersByTime(2000)
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(hook.result.current.events).toEqual([])
  })
})
