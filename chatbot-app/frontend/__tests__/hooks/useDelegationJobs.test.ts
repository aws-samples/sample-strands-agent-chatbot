import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDelegationJobs } from '@/hooks/useDelegationJobs'

vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: vi.fn().mockResolvedValue({
    tokens: {
      accessToken: {
        toString: () => 'delegation-token',
      },
    },
  }),
}))

function response(jobs: unknown[]) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ jobs }),
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

const job = {
  jobId: 'job-1',
  sessionId: 'session-1',
  userId: 'user-1',
  profile: 'analyst',
  executionStatus: 'running',
  deliveryStatus: 'none',
  request: {
    goal: 'Analyze data',
    deliverable: 'Report',
  },
  createdAt: '2026-08-10T00:00:00Z',
  updatedAt: '2026-08-10T00:00:00Z',
}

describe('useDelegationJobs', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('keeps polling while a completed job is waiting for mailbox delivery', async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response([job]))
      .mockImplementationOnce(() => response([{
        ...job,
        executionStatus: 'succeeded',
        deliveryStatus: 'published',
        updatedAt: '2026-08-10T00:00:01Z',
      }]))
      .mockImplementationOnce(() => response([{
        ...job,
        executionStatus: 'succeeded',
        deliveryStatus: 'delivered',
        updatedAt: '2026-08-10T00:00:02Z',
      }]))
    vi.stubGlobal('fetch', fetchMock)

    const hook = renderHook(() => useDelegationJobs('session-1'))
    await flushAsyncWork()

    await advance(2000)
    expect(hook.result.current.hasPendingDelivery).toBe(true)
    expect(hook.result.current.deliveryVersion).toBe(1)

    await advance(2000)
    expect(hook.result.current.hasPendingDelivery).toBe(false)
    expect(hook.result.current.deliveryVersion).toBe(2)
    expect(fetchMock).toHaveBeenCalledTimes(3)

    await advance(60000)
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('emits a delivery refresh when a poll observes delivered directly', async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response([job]))
      .mockImplementationOnce(() => response([{
        ...job,
        executionStatus: 'succeeded',
        deliveryStatus: 'delivered',
        updatedAt: '2026-08-10T00:00:01Z',
      }]))
    vi.stubGlobal('fetch', fetchMock)

    const hook = renderHook(() => useDelegationJobs('session-1'))
    await flushAsyncWork()

    await advance(2000)

    expect(hook.result.current.hasPendingDelivery).toBe(false)
    expect(hook.result.current.deliveryVersion).toBe(1)
  })
})
