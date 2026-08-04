import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  extractUserFromRequest: vi.fn(),
  getSessionId: vi.fn(),
  ensureSessionExists: vi.fn(),
  invokeAgentCoreRuntime: vi.fn(),
  createDefaultHookManager: vi.fn(),
  getUserDisabledSkills: vi.fn(),
}))

vi.mock('@/lib/auth-utils', () => ({
  extractUserFromRequest: mocks.extractUserFromRequest,
  getSessionId: mocks.getSessionId,
  ensureSessionExists: mocks.ensureSessionExists,
}))

vi.mock('@/lib/agentcore-runtime-client', () => ({
  invokeAgentCoreRuntime: mocks.invokeAgentCoreRuntime,
}))

vi.mock('@/lib/chat-hooks', () => ({
  createDefaultHookManager: mocks.createDefaultHookManager,
}))

vi.mock('@/lib/dynamodb-client', () => ({
  getUserDisabledSkills: mocks.getUserDisabledSkills,
}))

vi.mock('sharp', () => ({ default: vi.fn() }))

function request(state: Record<string, unknown>) {
  return {
    headers: { get: vi.fn().mockReturnValue(null) },
    signal: { addEventListener: vi.fn() },
    json: vi.fn().mockResolvedValue({
      threadId: 'session-1',
      runId: 'run-1',
      messages: [{ id: 'm1', role: 'user', content: 'hi' }],
      tools: [],
      context: [],
      state,
    }),
  }
}

/** Drain the streamed response so the handler body actually runs. */
async function drain(response: Response) {
  const reader = response.body?.getReader()
  if (!reader) return
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done } = await reader.read()
    if (done) break
  }
}

function forwardedState() {
  expect(mocks.invokeAgentCoreRuntime).toHaveBeenCalled()
  return mocks.invokeAgentCoreRuntime.mock.calls[0][0].state as Record<string, unknown>
}

describe('POST /api/stream/chat — 3LO federation opt-out', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    vi.stubEnv('NEXT_PUBLIC_AGENTCORE_LOCAL', 'true')
    mocks.extractUserFromRequest.mockResolvedValue({ userId: 'user-1' })
    mocks.getSessionId.mockReturnValue('session-1')
    mocks.ensureSessionExists.mockResolvedValue({ isNew: false })
    mocks.createDefaultHookManager.mockReturnValue({
      executeBeforeHooks: vi.fn().mockResolvedValue(undefined),
      executeAfterHooks: vi.fn().mockResolvedValue(undefined),
    })
    mocks.invokeAgentCoreRuntime.mockResolvedValue(
      new ReadableStream({ start: (c) => c.close() })
    )
  })

  // Mobile has no OAuth callback page, so it sends allow_user_federation=false.
  // The BFF rebuilds state from scratch — the flag has to be carried across.
  it('forwards allow_user_federation=false to the runtime', async () => {
    const { POST } = await import('@/app/api/stream/chat/route')
    await drain(await POST(request({ allow_user_federation: false }) as never))

    expect(forwardedState().allow_user_federation).toBe(false)
  })

  it('omits the flag when the client does not opt out', async () => {
    const { POST } = await import('@/app/api/stream/chat/route')
    await drain(await POST(request({}) as never))

    expect(forwardedState()).not.toHaveProperty('allow_user_federation')
  })

  it('treats a truthy value as the default rather than an opt-out', async () => {
    const { POST } = await import('@/app/api/stream/chat/route')
    await drain(await POST(request({ allow_user_federation: true }) as never))

    expect(forwardedState()).not.toHaveProperty('allow_user_federation')
  })
})
