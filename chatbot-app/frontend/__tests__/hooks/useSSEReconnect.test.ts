import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useSSEReconnect } from '@/hooks/useSSEReconnect'

vi.mock('@/config/environment', () => ({
  getApiUrl: (path: string) => `http://localhost:3000/api/${path}`,
}))

function statusResponse(status = 'running') {
  return {
    ok: true,
    json: async () => ({ status }),
  } as Response
}

function streamResponse(payload: string) {
  let sent = false
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { done: true, value: undefined }
          sent = true
          return {
            done: false,
            value: new TextEncoder().encode(payload),
          }
        },
        cancel: vi.fn().mockResolvedValue(undefined),
        releaseLock: vi.fn(),
      }),
    },
  } as unknown as Response
}

describe('useSSEReconnect', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('preserves SSE event and execution identities during replay', async () => {
    const executionId = 'session-a:run-1'
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(statusResponse())
      .mockResolvedValueOnce(streamResponse([
        'id: 7',
        'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"message-1","delta":"Hello"}',
        '',
        'id: 8',
        'data: {"type":"RUN_FINISHED","threadId":"session-a","runId":"run-1"}',
        '',
      ].join('\n')))
    vi.stubGlobal('fetch', fetchMock)

    const onEvent = vi.fn()
    const onComplete = vi.fn()
    const hook = renderHook(() => useSSEReconnect())

    act(() => hook.result.current.onStreamStart(executionId))
    await act(async () => {
      await hook.result.current.attemptReconnect(
        onEvent,
        onComplete,
        vi.fn(),
        async () => ({ Authorization: 'Bearer test' }),
      )
    })

    expect(onEvent).toHaveBeenNthCalledWith(1, expect.objectContaining({
      type: 'TEXT_MESSAGE_CONTENT',
      _eventId: 7,
      _executionId: executionId,
    }))
    expect(onEvent).toHaveBeenNthCalledWith(2, expect.objectContaining({
      type: 'RUN_FINISHED',
      _eventId: 8,
      _executionId: executionId,
    }))
    expect(onComplete).toHaveBeenCalledOnce()
  })

  it('serializes asynchronous event cleanup before completing replay', async () => {
    const executionId = 'session-a:run-serial'
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(statusResponse())
      .mockResolvedValueOnce(streamResponse([
        'id: 1',
        'data: {"type":"RUN_FINISHED","threadId":"session-a","runId":"run-serial"}',
        '',
      ].join('\n')))
    vi.stubGlobal('fetch', fetchMock)
    let releaseCleanup: (() => void) | undefined
    const cleanup = new Promise<void>(resolve => {
      releaseCleanup = resolve
    })
    const onEvent = vi.fn().mockReturnValue(cleanup)
    const onComplete = vi.fn()
    const hook = renderHook(() => useSSEReconnect())

    act(() => hook.result.current.onStreamStart(executionId))
    let replay: Promise<void>
    act(() => {
      replay = hook.result.current.attemptReconnect(
        onEvent,
        onComplete,
        vi.fn(),
        async () => ({}),
      )
    })
    await waitFor(() => expect(onEvent).toHaveBeenCalledOnce())
    expect(onComplete).not.toHaveBeenCalled()

    await act(async () => {
      releaseCleanup?.()
      await replay!
    })

    expect(onComplete).toHaveBeenCalledOnce()
  })

  it('does not dispatch replay events after the consumer is detached', async () => {
    const executionId = 'session-a:run-2'
    let resolveRead: ((value: ReadableStreamReadResult<Uint8Array>) => void) | undefined
    const cancel = vi.fn().mockResolvedValue(undefined)
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(statusResponse())
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        body: {
          getReader: () => ({
            read: () => new Promise<ReadableStreamReadResult<Uint8Array>>(resolve => {
              resolveRead = resolve
            }),
            cancel,
            releaseLock: vi.fn(),
          }),
        },
      } as unknown as Response)
    vi.stubGlobal('fetch', fetchMock)

    const onEvent = vi.fn()
    const onComplete = vi.fn()
    const onFail = vi.fn()
    const hook = renderHook(() => useSSEReconnect())

    act(() => hook.result.current.onStreamStart(executionId))
    let reconnectPromise: Promise<void>
    act(() => {
      reconnectPromise = hook.result.current.attemptReconnect(
        onEvent,
        onComplete,
        onFail,
        async () => ({}),
      )
    })
    await waitFor(() => expect(resolveRead).toBeDefined())

    act(() => hook.result.current.detach())
    await act(async () => {
      resolveRead?.({
        done: false,
        value: new TextEncoder().encode(
          'id: 1\ndata: {"type":"RUN_STARTED","threadId":"session-a","runId":"run-2"}\n\n',
        ),
      })
      await reconnectPromise!
    })

    expect(cancel).toHaveBeenCalledOnce()
    expect(onEvent).not.toHaveBeenCalled()
    expect(onComplete).not.toHaveBeenCalled()
    expect(onFail).not.toHaveBeenCalled()
    expect(sessionStorage.removeItem).not.toHaveBeenCalled()
  })

  it('does not let an idle session clear another session replay ID', () => {
    const hook = renderHook(() => useSSEReconnect())

    act(() => {
      hook.result.current.onStreamStart('session-a:run-3')
      hook.result.current.detach()
    })
    expect(hook.result.current.restoreFromSession('session-b')).toBe(false)

    act(() => hook.result.current.reset())

    expect(sessionStorage.removeItem).not.toHaveBeenCalledWith(
      'sse_exec_session-a:run-3',
    )
  })
})
