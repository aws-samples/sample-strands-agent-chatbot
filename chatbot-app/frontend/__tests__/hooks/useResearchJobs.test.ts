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

    hook.rerender({ invocationCount: 1 })
    await flushAsyncWork()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(hook.result.current.jobs[0]?.status).toBe('running')

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

    await act(async () => {
      vi.advanceTimersByTime(2000)
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(hook.result.current.jobs[0]?.status).toBe('running')
  })
})
