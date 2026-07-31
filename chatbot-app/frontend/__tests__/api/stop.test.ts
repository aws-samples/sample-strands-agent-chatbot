import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  extractUserFromRequest: vi.fn(),
  getSessionId: vi.fn(),
  getSession: vi.fn(),
  writeStopSignal: vi.fn(),
}))

vi.mock('@/lib/auth-utils', () => ({
  extractUserFromRequest: mocks.extractUserFromRequest,
  getSessionId: mocks.getSessionId,
}))

vi.mock('@/lib/dynamodb-client', () => ({
  getSession: mocks.getSession,
  writeStopSignal: mocks.writeStopSignal,
}))

function request(body: Record<string, unknown>) {
  return {
    json: vi.fn().mockResolvedValue(body),
    headers: { get: vi.fn().mockReturnValue(null) },
  }
}

describe('POST /api/stream/stop', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    vi.stubEnv('NEXT_PUBLIC_AGENTCORE_LOCAL', 'false')
    mocks.extractUserFromRequest.mockResolvedValue({ userId: 'user-1' })
  })

  it('rejects a request without a run ID', async () => {
    const { POST } = await import('@/app/api/stream/stop/route')
    const response = await POST(request({ sessionId: 'session-1' }) as never)

    expect(response.status).toBe(400)
    expect(mocks.writeStopSignal).not.toHaveBeenCalled()
  })

  it('rejects a session the user does not own', async () => {
    mocks.getSession.mockResolvedValue(null)
    const { POST } = await import('@/app/api/stream/stop/route')
    const response = await POST(
      request({ sessionId: 'session-1', runId: 'run-1' }) as never,
    )

    expect(response.status).toBe(404)
    expect(mocks.writeStopSignal).not.toHaveBeenCalled()
  })

  it('writes a run-scoped signal after validating the session', async () => {
    mocks.getSession.mockResolvedValue({ sessionId: 'session-1' })
    const { POST } = await import('@/app/api/stream/stop/route')
    const response = await POST(
      request({ sessionId: 'session-1', runId: 'run-1' }) as never,
    )

    expect(response.status).toBe(200)
    expect(mocks.getSession).toHaveBeenCalledWith('user-1', 'session-1')
    expect(mocks.writeStopSignal).toHaveBeenCalledWith(
      'user-1',
      'session-1',
      'run-1',
    )
  })

  it('only acknowledges a local stop accepted by the runtime', async () => {
    vi.stubEnv('NEXT_PUBLIC_AGENTCORE_LOCAL', 'true')
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ status: 'stop_unavailable' }),
    } as never)

    const { POST } = await import('@/app/api/stream/stop/route')
    const response = await POST(
      request({ sessionId: 'session-1', runId: 'run-1' }) as never,
    )

    expect(response.status).toBe(503)
  })
})
