/**
 * Message queue for turns typed while the agent is still running.
 *
 * The composer stays enabled during a run; anything submitted goes here instead
 * of being sent, and is flushed once the current turn genuinely finishes.
 *
 * Why the queue owns no send logic and no lifecycle observation: flushing is
 * only safe at one point in the turn, and `agentStatus === 'idle'` does not
 * identify it. Four paths in useStreamEvents set 'idle' (RUN_FINISHED,
 * RUN_ERROR, interrupt, resetStreamingState) and two of them mean "the agent is
 * parked waiting on the user", not "the turn is over":
 *
 *   - interrupt (HITL tool approval) parks the run mid-turn with a toolUse that
 *     has no toolResult yet. Sending a user message there corrupts the message
 *     history and Bedrock rejects the next call with a ValidationException.
 *   - pendingOAuth leaves the backend blocked inside elicitation_bridge for up
 *     to 300s while the frontend already reports 'idle'.
 *
 * A turn that ends in an interrupt still closes its SSE stream normally, so the
 * send path's success callback fires in that case too. The caller therefore
 * passes the blockers it knows about on every flush attempt, and must read them
 * after React has committed the events from that stream — see `flushNext`.
 */
import { useCallback, useRef, useState } from 'react'

export interface QueuedMessage {
  id: string
  text: string
  files: File[]
  /** Session the message was composed in; a queued item never crosses sessions. */
  sessionId: string
  /**
   * Artifact context captured at enqueue time rather than resolved at flush
   * time, so the message is sent with the context the user was looking at when
   * they typed it.
   */
  systemPrompt?: string
  selectedArtifactId?: string | null
}

/** Why the queue is holding instead of flushing. Drives the composer's prompt. */
export type QueueHoldReason = 'error' | 'stopped' | 'interrupt' | 'oauth'

export interface FlushBlockers {
  /** A HITL tool approval is awaiting the user — the run is parked mid-turn. */
  hasInterrupt: boolean
  /** An OAuth elicitation is awaiting the user; the backend is still blocked. */
  hasPendingOAuth: boolean
}

export interface UseMessageQueueReturn {
  queue: QueuedMessage[]
  /** Set when a turn ended abnormally: queued items are kept but not auto-sent. */
  holdReason: QueueHoldReason | null
  enqueue: (message: Omit<QueuedMessage, 'id'>) => void
  remove: (id: string) => void
  clear: () => void
  /**
   * Send the next queued message for `sessionId` if it is safe to do so.
   * Must be called after React has committed the finishing turn's events, so
   * that `blockers` reflects an interrupt or elicitation raised by that turn.
   * Returns true when a message was dispatched.
   */
  flushNext: (sessionId: string, blockers: FlushBlockers) => Promise<boolean>
  /** Record that a turn ended abnormally, so the queue waits for confirmation. */
  hold: (reason: QueueHoldReason) => void
  /** User confirmed a held queue: resume automatic flushing. */
  release: () => void
  /** Drop items belonging to other sessions (called on session switch). */
  retainSession: (sessionId: string) => void
}

interface UseMessageQueueProps {
  /** Dispatches a queued message through the normal send path. */
  send: (
    text: string,
    files: File[],
    systemPrompt?: string,
    selectedArtifactId?: string | null,
  ) => Promise<void>
}

export function useMessageQueue({ send }: UseMessageQueueProps): UseMessageQueueReturn {
  const [queue, setQueue] = useState<QueuedMessage[]>([])
  const [holdReason, setHoldReason] = useState<QueueHoldReason | null>(null)

  // Mirrors of the state above, for reads inside flushNext. flushNext runs from
  // an effect that fires on turn completion, so a closed-over render value would
  // be stale by then.
  const queueRef = useRef<QueuedMessage[]>([])
  const holdReasonRef = useRef<QueueHoldReason | null>(null)

  const updateQueue = useCallback((next: (prev: QueuedMessage[]) => QueuedMessage[]) => {
    setQueue(prev => {
      const value = next(prev)
      queueRef.current = value
      return value
    })
  }, [])

  const updateHold = useCallback((reason: QueueHoldReason | null) => {
    holdReasonRef.current = reason
    setHoldReason(reason)
  }, [])

  // Guards overlapping flushes. The send path aborts any in-flight stream as its
  // first step, so a second concurrent flush would tear down the stream the
  // first one is still reading.
  const isFlushingRef = useRef(false)

  const enqueue = useCallback((message: Omit<QueuedMessage, 'id'>) => {
    const text = message.text.trim()
    if (!text && message.files.length === 0) return
    updateQueue(prev => [...prev, { ...message, text, id: crypto.randomUUID() }])
  }, [updateQueue])

  const remove = useCallback((id: string) => {
    updateQueue(prev => {
      const next = prev.filter(m => m.id !== id)
      // A hold only means something while items remain to confirm.
      if (next.length === 0) updateHold(null)
      return next
    })
  }, [updateQueue, updateHold])

  const clear = useCallback(() => {
    updateQueue(() => [])
    updateHold(null)
  }, [updateQueue, updateHold])

  const hold = useCallback((reason: QueueHoldReason) => {
    if (queueRef.current.length === 0) return
    updateHold(reason)
  }, [updateHold])

  const release = useCallback(() => updateHold(null), [updateHold])

  const retainSession = useCallback((sessionId: string) => {
    updateQueue(prev => {
      const next = prev.filter(m => m.sessionId === sessionId)
      if (next.length === 0) updateHold(null)
      return next
    })
  }, [updateQueue, updateHold])

  const flushNext = useCallback(async (
    sessionId: string,
    blockers: FlushBlockers,
  ): Promise<boolean> => {
    if (isFlushingRef.current) return false
    if (holdReasonRef.current) return false
    if (!queueRef.current.some(m => m.sessionId === sessionId)) return false

    // The agent is parked waiting on the user. Sending now would either corrupt
    // the message history (interrupt) or race the authorization (oauth), so keep
    // the queue and surface it once the user has dealt with the prompt.
    if (blockers.hasInterrupt) {
      hold('interrupt')
      return false
    }
    if (blockers.hasPendingOAuth) {
      hold('oauth')
      return false
    }

    const next = queueRef.current.find(m => m.sessionId === sessionId)!

    isFlushingRef.current = true
    // Dequeue before sending: the send path echoes the text into the transcript
    // immediately, so leaving it queued on failure would offer a duplicate.
    updateQueue(prev => prev.filter(m => m.id !== next.id))
    try {
      await send(next.text, next.files, next.systemPrompt, next.selectedArtifactId)
      return true
    } finally {
      isFlushingRef.current = false
    }
  }, [send, hold, updateQueue])

  return {
    queue,
    holdReason,
    enqueue,
    remove,
    clear,
    flushNext,
    hold,
    release,
    retainSession,
  }
}
