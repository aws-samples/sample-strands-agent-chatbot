import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useMessageQueue, FlushBlockers } from '@/hooks/useMessageQueue'

const SESSION = 'session-1'
const OTHER_SESSION = 'session-2'

const CLEAR: FlushBlockers = { hasInterrupt: false, hasPendingOAuth: false }

function setup(send = vi.fn().mockResolvedValue(undefined)) {
  const hook = renderHook(() => useMessageQueue({ send }))
  return { hook, send }
}

function enqueue(
  hook: ReturnType<typeof setup>['hook'],
  text: string,
  sessionId = SESSION,
) {
  act(() => {
    hook.result.current.enqueue({ text, files: [], sessionId })
  })
}

describe('useMessageQueue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('queues messages and preserves FIFO order across flushes', async () => {
    const { hook, send } = setup()
    enqueue(hook, 'first')
    enqueue(hook, 'second')

    expect(hook.result.current.queue.map(m => m.text)).toEqual(['first', 'second'])

    await act(async () => { await hook.result.current.flushNext(SESSION, CLEAR) })
    await act(async () => { await hook.result.current.flushNext(SESSION, CLEAR) })

    expect(send.mock.calls.map(c => c[0])).toEqual(['first', 'second'])
    expect(hook.result.current.queue).toHaveLength(0)
  })

  it('moves a selected message to the front for the next flush', async () => {
    const { hook, send } = setup()
    enqueue(hook, 'first')
    enqueue(hook, 'selected')
    enqueue(hook, 'third')
    const selectedId = hook.result.current.queue[1].id

    let prioritized = false
    act(() => {
      prioritized = hook.result.current.prioritize(selectedId, SESSION)
    })

    expect(prioritized).toBe(true)
    expect(hook.result.current.queue.map(m => m.text)).toEqual([
      'selected',
      'first',
      'third',
    ])

    await act(async () => { await hook.result.current.flushNext(SESSION, CLEAR) })
    expect(send).toHaveBeenCalledWith('selected', [], undefined, undefined)
  })

  it('does not prioritize a message from another session', () => {
    const { hook } = setup()
    enqueue(hook, 'foreign', OTHER_SESSION)
    const id = hook.result.current.queue[0].id

    let prioritized = true
    act(() => {
      prioritized = hook.result.current.prioritize(id, SESSION)
    })

    expect(prioritized).toBe(false)
    expect(hook.result.current.queue.map(m => m.text)).toEqual(['foreign'])
  })

  it('ignores empty submissions but keeps attachment-only ones', () => {
    const { hook } = setup()
    enqueue(hook, '   ')
    expect(hook.result.current.queue).toHaveLength(0)

    act(() => {
      hook.result.current.enqueue({
        text: '',
        files: [new File(['x'], 'a.txt')],
        sessionId: SESSION,
      })
    })
    expect(hook.result.current.queue).toHaveLength(1)
  })

  it('carries the artifact context captured at enqueue time', async () => {
    const { hook, send } = setup()
    act(() => {
      hook.result.current.enqueue({
        text: 'hi',
        files: [],
        sessionId: SESSION,
        systemPrompt: 'artifact-ctx',
        selectedArtifactId: 'artifact-1',
      })
    })

    await act(async () => { await hook.result.current.flushNext(SESSION, CLEAR) })

    expect(send).toHaveBeenCalledWith('hi', [], 'artifact-ctx', 'artifact-1')
  })

  // A turn that stops at a HITL approval still closes its SSE stream normally,
  // so the send path reports success. Flushing there would append a user message
  // to a history whose toolUse has no toolResult, which Bedrock rejects.
  it('does not send while a tool approval is pending, and holds instead', async () => {
    const { hook, send } = setup()
    enqueue(hook, 'queued')

    await act(async () => {
      await hook.result.current.flushNext(SESSION, { ...CLEAR, hasInterrupt: true })
    })

    expect(send).not.toHaveBeenCalled()
    expect(hook.result.current.queue).toHaveLength(1)
    expect(hook.result.current.holdReason).toBe('interrupt')
  })

  it('does not send while an OAuth elicitation is pending', async () => {
    const { hook, send } = setup()
    enqueue(hook, 'queued')

    await act(async () => {
      await hook.result.current.flushNext(SESSION, { ...CLEAR, hasPendingOAuth: true })
    })

    expect(send).not.toHaveBeenCalled()
    expect(hook.result.current.holdReason).toBe('oauth')
  })

  it('stays held until released, then sends', async () => {
    const { hook, send } = setup()
    enqueue(hook, 'queued')
    act(() => { hook.result.current.hold('stopped') })

    await act(async () => { await hook.result.current.flushNext(SESSION, CLEAR) })
    expect(send).not.toHaveBeenCalled()

    act(() => { hook.result.current.release() })
    await act(async () => { await hook.result.current.flushNext(SESSION, CLEAR) })

    expect(send).toHaveBeenCalledWith('queued', [], undefined, undefined)
  })

  it('re-holds when released while an approval is still pending', async () => {
    const { hook, send } = setup()
    enqueue(hook, 'queued')
    act(() => { hook.result.current.hold('stopped') })
    act(() => { hook.result.current.release() })

    await act(async () => {
      await hook.result.current.flushNext(SESSION, { ...CLEAR, hasInterrupt: true })
    })

    expect(send).not.toHaveBeenCalled()
    expect(hook.result.current.holdReason).toBe('interrupt')
  })

  it('does not hold when nothing is queued', async () => {
    const { hook } = setup()
    act(() => { hook.result.current.hold('error') })
    expect(hook.result.current.holdReason).toBeNull()
  })

  it('clears the hold once the last queued message is removed', () => {
    const { hook } = setup()
    enqueue(hook, 'queued')
    act(() => { hook.result.current.hold('error') })
    expect(hook.result.current.holdReason).toBe('error')

    act(() => { hook.result.current.remove(hook.result.current.queue[0].id) })

    expect(hook.result.current.queue).toHaveLength(0)
    expect(hook.result.current.holdReason).toBeNull()
  })

  // The send path aborts any in-flight stream as its first step, so a second
  // concurrent flush would tear down the stream the first one is reading.
  it('serializes concurrent flushes', async () => {
    let release: () => void = () => {}
    const send = vi.fn().mockImplementation(
      () => new Promise<void>(resolve => { release = resolve }),
    )
    const { hook } = setup(send)
    enqueue(hook, 'first')
    enqueue(hook, 'second')

    await act(async () => {
      const a = hook.result.current.flushNext(SESSION, CLEAR)
      const b = hook.result.current.flushNext(SESSION, CLEAR)
      expect(await b).toBe(false)
      release()
      expect(await a).toBe(true)
    })

    expect(send).toHaveBeenCalledTimes(1)
  })

  it('removes a dispatched message before its response completes', async () => {
    let finish: () => void = () => {}
    const send = vi.fn(
      () => new Promise<void>(resolve => { finish = resolve }),
    )
    const { hook } = setup(send)
    enqueue(hook, 'dispatched')
    enqueue(hook, 'still waiting')

    let flushPromise: Promise<boolean>
    act(() => {
      flushPromise = hook.result.current.flushNext(SESSION, CLEAR)
    })

    expect(send).toHaveBeenCalledWith('dispatched', [], undefined, undefined)
    expect(hook.result.current.queue.map(m => m.text)).toEqual(['still waiting'])

    await act(async () => {
      finish()
      await flushPromise!
    })
  })

  it('does not requeue an accepted message when its response fails', async () => {
    const send = vi.fn().mockRejectedValue(new Error('network'))
    const { hook } = setup(send)
    enqueue(hook, 'doomed')

    const sent = await act(async () => hook.result.current.flushNext(SESSION, CLEAR))

    expect(sent).toBe(false)
    expect(hook.result.current.queue).toEqual([])
    expect(hook.result.current.holdReason).toBeNull()
  })

  it('holds messages still waiting behind a failed dispatched turn', async () => {
    const send = vi.fn().mockRejectedValue(new Error('network'))
    const { hook } = setup(send)
    enqueue(hook, 'doomed')
    enqueue(hook, 'still queued')

    await act(async () => hook.result.current.flushNext(SESSION, CLEAR))

    expect(hook.result.current.queue.map(m => m.text)).toEqual(['still queued'])
    expect(hook.result.current.holdReason).toBe('error')
  })

  it('never sends a message into a different session', async () => {
    const { hook, send } = setup()
    enqueue(hook, 'for-other', OTHER_SESSION)

    const sent = await act(async () => hook.result.current.flushNext(SESSION, CLEAR))

    expect(sent).toBe(false)
    expect(send).not.toHaveBeenCalled()
    expect(hook.result.current.queue).toHaveLength(1)
  })

  // Reachable when the user switches away mid-run and back: the queue can hold
  // items from both sessions, so flush must select by session rather than
  // assuming the head of the queue belongs to the active one.
  it('skips foreign messages ahead of the current session in the queue', async () => {
    const { hook, send } = setup()
    enqueue(hook, 'foreign', OTHER_SESSION)
    enqueue(hook, 'mine', SESSION)

    await act(async () => { await hook.result.current.flushNext(SESSION, CLEAR) })

    expect(send).toHaveBeenCalledTimes(1)
    expect(send).toHaveBeenCalledWith('mine', [], undefined, undefined)
    expect(hook.result.current.queue.map(m => m.text)).toEqual(['foreign'])
  })

  it('drops other sessions queues on session switch', () => {
    const { hook } = setup()
    enqueue(hook, 'keep', SESSION)
    enqueue(hook, 'drop', OTHER_SESSION)

    act(() => { hook.result.current.retainSession(SESSION) })

    expect(hook.result.current.queue.map(m => m.text)).toEqual(['keep'])
  })

  it('clears the hold when the switch leaves the queue empty', () => {
    const { hook } = setup()
    enqueue(hook, 'drop', OTHER_SESSION)
    act(() => { hook.result.current.hold('error') })

    act(() => { hook.result.current.retainSession(SESSION) })

    expect(hook.result.current.queue).toHaveLength(0)
    expect(hook.result.current.holdReason).toBeNull()
  })
})
