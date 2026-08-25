import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  extractUserFromRequest: vi.fn(),
  getSessionId: vi.fn(),
  ensureSessionExists: vi.fn(),
  invokeAgentCoreRuntime: vi.fn(),
}))

vi.mock('@/lib/auth-utils', () => ({
  extractUserFromRequest: mocks.extractUserFromRequest,
  getSessionId: mocks.getSessionId,
  ensureSessionExists: mocks.ensureSessionExists,
}))

vi.mock('@/lib/agentcore-runtime-client', () => ({
  AgentCoreRuntimeError: class AgentCoreRuntimeError extends Error {},
  isAbortError: (error: unknown) => (
    typeof error === 'object'
    && error !== null
    && 'name' in error
    && error.name === 'AbortError'
  ),
  invokeAgentCoreRuntime: mocks.invokeAgentCoreRuntime,
}))

vi.mock('@/lib/chat-hooks', () => ({
  createDefaultHookManager: vi.fn(),
}))

vi.mock('sharp', () => ({ default: vi.fn() }))

function request() {
  return {
    headers: {
      get: vi.fn((name: string) => (
        name.toLowerCase() === 'authorization' ? 'Bearer stale-token' : null
      )),
    },
    signal: { addEventListener: vi.fn() },
    json: vi.fn().mockResolvedValue({
      threadId: 'session-1',
      runId: 'run-1',
      messages: [{ id: 'm1', role: 'user', content: 'hello' }],
      tools: [],
      context: [],
      state: {},
    }),
  }
}

describe('POST /api/stream/chat — Runtime token freshness', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    vi.stubEnv('NEXT_PUBLIC_AGENTCORE_LOCAL', 'false')
    vi.setSystemTime(new Date('2026-08-25T12:00:00Z'))
  })

  it('rejects a verified token with less than two minutes remaining before side effects', async () => {
    mocks.extractUserFromRequest.mockResolvedValue({
      userId: 'user-1',
      tokenExpiresAt: Date.now() / 1000 + 60,
    })
    const { POST } = await import('@/app/api/stream/chat/route')

    const response = await POST(request() as never)

    expect(response.status).toBe(401)
    await expect(response.json()).resolves.toMatchObject({
      code: 'AUTH_TOKEN_STALE',
    })
    expect(mocks.ensureSessionExists).not.toHaveBeenCalled()
    expect(mocks.invokeAgentCoreRuntime).not.toHaveBeenCalled()
  })
})
