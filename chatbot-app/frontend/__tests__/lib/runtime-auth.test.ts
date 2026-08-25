import { beforeEach, describe, expect, it, vi } from 'vitest'

const fetchAuthSession = vi.hoisted(() => vi.fn())

vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession,
}))

function session(token: string, expiresAtSeconds: number) {
  return {
    tokens: {
      accessToken: {
        payload: { exp: expiresAtSeconds },
        toString: () => token,
      },
    },
  }
}

describe('runtime-auth', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    vi.setSystemTime(new Date('2026-08-25T12:00:00Z'))
  })

  it('reuses a token with at least two minutes remaining', async () => {
    fetchAuthSession.mockResolvedValue(
      session('cached-token', Date.now() / 1000 + 121),
    )
    const { getRuntimeAuthHeaders } = await import('@/lib/runtime-auth')

    await expect(getRuntimeAuthHeaders()).resolves.toEqual({
      Authorization: 'Bearer cached-token',
    })
    expect(fetchAuthSession).toHaveBeenCalledTimes(1)
    expect(fetchAuthSession).toHaveBeenCalledWith()
  })

  it('force-refreshes a token inside the AgentCore validity window', async () => {
    fetchAuthSession
      .mockResolvedValueOnce(session('stale-token', Date.now() / 1000 + 119))
      .mockResolvedValueOnce(session('fresh-token', Date.now() / 1000 + 3600))
    const { getRuntimeAuthHeaders } = await import('@/lib/runtime-auth')

    await expect(getRuntimeAuthHeaders()).resolves.toEqual({
      Authorization: 'Bearer fresh-token',
    })
    expect(fetchAuthSession).toHaveBeenNthCalledWith(2, { forceRefresh: true })
  })

  it('coalesces concurrent forced refreshes', async () => {
    let finishRefresh: ((value: ReturnType<typeof session>) => void) | undefined
    const refresh = new Promise<ReturnType<typeof session>>(resolve => {
      finishRefresh = resolve
    })
    fetchAuthSession.mockImplementation((options?: { forceRefresh?: boolean }) => (
      options?.forceRefresh
        ? refresh
        : Promise.resolve(session('stale-token', Date.now() / 1000 + 30))
    ))
    const { getRuntimeAuthHeaders } = await import('@/lib/runtime-auth')

    const first = getRuntimeAuthHeaders()
    const second = getRuntimeAuthHeaders()
    await vi.waitFor(() => {
      expect(fetchAuthSession).toHaveBeenCalledWith({ forceRefresh: true })
    })
    finishRefresh?.(session('fresh-token', Date.now() / 1000 + 3600))

    await expect(Promise.all([first, second])).resolves.toEqual([
      { Authorization: 'Bearer fresh-token' },
      { Authorization: 'Bearer fresh-token' },
    ])
    expect(
      fetchAuthSession.mock.calls.filter(([options]) => options?.forceRefresh),
    ).toHaveLength(1)
  })

  it('rejects a refreshed token that is still too close to expiry', async () => {
    fetchAuthSession
      .mockResolvedValueOnce(session('stale-token', Date.now() / 1000 + 30))
      .mockResolvedValueOnce(session('still-stale', Date.now() / 1000 + 60))
    const { getRuntimeAccessToken } = await import('@/lib/runtime-auth')

    await expect(getRuntimeAccessToken()).rejects.toThrow(
      'not valid long enough',
    )
  })
})
