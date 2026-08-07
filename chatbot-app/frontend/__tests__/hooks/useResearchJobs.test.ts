import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useResearchJobs } from '@/hooks/useResearchJobs'

vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: vi.fn().mockResolvedValue({
    tokens: {
      accessToken: {
        toString: () => 'research-access-token',
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

const runningJob = {
  jobId: 'job-1',
  sessionId: 'session-1',
  userId: 'user-1',
  artifactId: 'research-tool-1',
  plan: 'Research',
  status: 'running',
  createdAt: '2026-08-06T00:00:00Z',
  updatedAt: '2026-08-06T00:00:01Z',
}

const deliveredJob = {
  ...runningJob,
  status: 'delivered',
  updatedAt: '2026-08-06T00:00:02Z',
  artifact: {
    id: 'research-tool-1',
    type: 'research',
    title: 'Report',
  },
}

const completedJob = {
  ...runningJob,
  status: 'completed',
  updatedAt: '2026-08-06T00:00:02Z',
  artifact: {
    id: 'research-tool-1',
    type: 'research',
    title: 'Report',
  },
}

describe('useResearchJobs', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('discovers a job after its tool invocation and hydrates its completion', async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response([]))
      .mockImplementationOnce(() => response([runningJob]))
      .mockImplementationOnce(() => response([deliveredJob]))
      .mockImplementationOnce(() => response([{
        ...deliveredJob,
        artifact: {
          ...deliveredJob.artifact,
          content: '# Finished report',
        },
      }]))
    vi.stubGlobal('fetch', fetchMock)

    const hook = renderHook(
      ({ invocationCount }) => useResearchJobs('session-1', invocationCount),
      { initialProps: { invocationCount: 0 } },
    )

    await flushAsyncWork()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(hook.result.current.isActive).toBe(false)

    hook.rerender({ invocationCount: 1 })
    await flushAsyncWork()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(hook.result.current.jobs[0]?.status).toBe('running')
    expect(hook.result.current.isActive).toBe(true)
    expect(hook.result.current.hasPendingDelivery).toBe(false)

    await act(async () => {
      vi.advanceTimersByTime(2000)
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(fetchMock).toHaveBeenCalledTimes(4)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/research/jobs?session_id=session-1',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer research-access-token',
        }),
      }),
    )
    expect(hook.result.current.jobs[0]?.artifact?.content).toBe('# Finished report')
    expect(hook.result.current.deliveredJobIds).toEqual(['job-1'])
    expect(hook.result.current.isActive).toBe(false)
    expect(hook.result.current.hasPendingDelivery).toBe(false)
    expect(hook.result.current.deliveryVersion).toBe(1)
  })

  it('keeps polling during invocation discovery when the first lookup is empty', async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response([]))
      .mockImplementationOnce(() => response([]))
      .mockImplementationOnce(() => response([runningJob]))
    vi.stubGlobal('fetch', fetchMock)

    const hook = renderHook(
      ({ invocationCount }) => useResearchJobs('session-1', invocationCount),
      { initialProps: { invocationCount: 0 } },
    )

    await flushAsyncWork()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    hook.rerender({ invocationCount: 1 })
    await flushAsyncWork()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(hook.result.current.isActive).toBe(true)

    await act(async () => {
      vi.advanceTimersByTime(2000)
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(hook.result.current.jobs[0]?.status).toBe('running')
  })

  it('ends discovery polling when a new invocation never gets a job row', async () => {
    const fetchMock = vi.fn(() => response([]))
    vi.stubGlobal('fetch', fetchMock)

    const hook = renderHook(
      ({ invocationCount }) => useResearchJobs('session-1', invocationCount),
      { initialProps: { invocationCount: 0 } },
    )
    await flushAsyncWork()

    hook.rerender({ invocationCount: 1 })
    await flushAsyncWork()
    expect(hook.result.current.isActive).toBe(true)

    for (let tick = 0; tick < 8; tick += 1) {
      await act(async () => {
        vi.advanceTimersByTime(2000)
        await Promise.resolve()
        await Promise.resolve()
      })
    }

    expect(hook.result.current.isActive).toBe(false)
    expect(hook.result.current.hasPendingDelivery).toBe(false)
    const callsAfterDiscovery = fetchMock.mock.calls.length
    await act(async () => {
      vi.advanceTimersByTime(10000)
      await Promise.resolve()
    })
    expect(fetchMock).toHaveBeenCalledTimes(callsAfterDiscovery)
  })

  it('treats invocations present at session load as a historical baseline', async () => {
    const fetchMock = vi.fn(() => response([]))
    vi.stubGlobal('fetch', fetchMock)

    const hook = renderHook(() => useResearchJobs('session-1', 3))
    await flushAsyncWork()

    expect(hook.result.current.isActive).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await act(async () => {
      vi.advanceTimersByTime(30000)
      await Promise.resolve()
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('hydrates a completed job without keeping fast polling active', async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response([completedJob]))
      .mockImplementationOnce(() => response([{
        ...completedJob,
        artifact: {
          ...completedJob.artifact,
          content: '# Finished report',
        },
      }]))
    vi.stubGlobal('fetch', fetchMock)

    const hook = renderHook(() => useResearchJobs('session-1', 1))
    await flushAsyncWork()

    expect(hook.result.current.jobs[0]?.artifact?.content).toBe('# Finished report')
    expect(hook.result.current.isActive).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(2)

    await act(async () => {
      vi.advanceTimersByTime(10000)
      await Promise.resolve()
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('hides the previous session snapshot immediately when switching sessions', async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response([runningJob]))
      .mockImplementationOnce(() => new Promise<Response>(() => {}))
    vi.stubGlobal('fetch', fetchMock)

    const hook = renderHook(
      ({ sessionId }) => useResearchJobs(sessionId),
      { initialProps: { sessionId: 'session-1' } },
    )
    await flushAsyncWork()
    expect(hook.result.current.jobs).toEqual([runningJob])

    hook.rerender({ sessionId: 'session-2' })

    expect(hook.result.current.jobs).toEqual([])
    expect(hook.result.current.deliveredJobIds).toEqual([])
    expect(hook.result.current.isActive).toBe(false)
  })
})
