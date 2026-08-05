/**
 * The BFF forwards concise mode as a flag and nothing more.
 *
 * The style itself is applied in the runtime, which swaps its own base-prompt
 * style sections. An earlier version appended a "be brief" block here, which
 * left it competing with those base instructions; these tests pin that the BFF
 * no longer injects prompt text and that the flag still reaches the runtime.
 */
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

const prompt = () => forwardedState().system_prompt as string

async function post(state: Record<string, unknown>) {
  const { POST } = await import('@/app/api/stream/chat/route')
  await drain(await POST(request(state) as never))
}

describe('POST /api/stream/chat — concise mode', () => {
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

  it('forwards the flag so the runtime can swap its style sections', async () => {
    await post({ concise_mode: true })
    expect(forwardedState().concise_mode).toBe(true)
  })

  it('omits the flag when off', async () => {
    await post({})
    expect(forwardedState()).not.toHaveProperty('concise_mode')
  })

  // Only an explicit true enables it; a stray truthy string must not.
  it('requires the flag to be exactly true', async () => {
    await post({ concise_mode: 'yes' })
    expect(forwardedState()).not.toHaveProperty('concise_mode')
  })

  // The style now lives in the runtime. If the BFF also injected it, both sets of
  // instructions would be in play again — the exact problem this replaced.
  it('injects no style prompt text of its own', async () => {
    await post({ concise_mode: true })
    const prompt = forwardedState().system_prompt as string
    expect(prompt).not.toMatch(/Response style/i)
    expect(prompt).not.toMatch(/No preamble/i)
  })

  it('still forwards the artifact context alongside the flag', async () => {
    await post({ concise_mode: true, system_prompt: 'ARTIFACT_CONTEXT_MARKER' })
    expect(forwardedState().system_prompt as string).toContain('ARTIFACT_CONTEXT_MARKER')
  })

  it('keeps the base prompt when concise is on', async () => {
    await post({})
    const base = forwardedState().system_prompt as string

    vi.clearAllMocks()
    mocks.invokeAgentCoreRuntime.mockResolvedValue(
      new ReadableStream({ start: (c) => c.close() })
    )
    await post({ concise_mode: true })

    expect(forwardedState().system_prompt).toBe(base)
  })
})
