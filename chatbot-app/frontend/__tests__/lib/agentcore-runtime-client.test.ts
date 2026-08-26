import { beforeEach, describe, expect, it, vi } from 'vitest'

describe('agentcore-runtime-client errors', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    vi.stubEnv('NEXT_PUBLIC_AGENTCORE_LOCAL', 'false')
    vi.stubEnv('AGENTCORE_RUNTIME_URL', 'https://runtime.example/invocations')
  })

  it('preserves AbortError so client disconnects are not reported as failures', async () => {
    const abortError = new DOMException('This operation was aborted', 'AbortError')
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(abortError))
    const { invokeAgentCoreRuntime } = await import('@/lib/agentcore-runtime-client')

    await expect(
      invokeAgentCoreRuntime({}, 'user-1', 'session-1', 'Bearer token'),
    ).rejects.toMatchObject({ name: 'AbortError' })
    expect(consoleError).not.toHaveBeenCalled()
  })

  it('preserves Runtime status and response details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response('{"error":"stale"}', { status: 401 }),
    ))
    const {
      AgentCoreRuntimeError,
      invokeAgentCoreRuntime,
    } = await import('@/lib/agentcore-runtime-client')

    const error = await invokeAgentCoreRuntime(
      {},
      'user-1',
      'session-1',
      'Bearer token',
    ).catch(value => value)

    expect(error).toBeInstanceOf(AgentCoreRuntimeError)
    expect(error).toMatchObject({
      status: 401,
      responseBody: '{"error":"stale"}',
    })
  })
})
