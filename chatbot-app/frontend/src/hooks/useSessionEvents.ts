import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api-client'
import type { SessionEventProjection } from '@/lib/session-events'

const ACTIVE_POLL_INTERVAL_MS = 2000

export function useSessionEvents(
  sessionId: string,
  hasPendingDelivery = false,
  deliveryVersion = 0,
  refreshVersion = 0,
) {
  const [events, setEvents] = useState<SessionEventProjection[]>([])
  const [snapshotSessionId, setSnapshotSessionId] = useState(sessionId)
  const seenRef = useRef<Set<string> | null>(null)
  const cursorRef = useRef<string | null>(null)
  const conversationEpochRef = useRef<number | null>(null)
  const refreshingRef = useRef(false)
  const sessionRef = useRef(sessionId)
  sessionRef.current = sessionId

  const refresh = useCallback(async (): Promise<boolean> => {
    if (!sessionId || refreshingRef.current) return false
    const requestedSessionId = sessionId
    refreshingRef.current = true
    try {
      const collected: SessionEventProjection[] = []
      let cursor = cursorRef.current
      let epoch = conversationEpochRef.current
      let hasMore = false
      let epochChanged = false
      do {
        const params = new URLSearchParams({ session_id: sessionId })
        if (cursor) params.set('cursor', cursor)
        if (epoch !== null) params.set('epoch', String(epoch))
        const response = await apiFetch(
          `session/events?${params.toString()}`,
          { cache: 'no-store' },
        )
        if (!response.ok) return false
        const data = await response.json()
        if (sessionRef.current !== requestedSessionId) return false
        const pageEpoch = Number.isFinite(Number(data.conversationEpoch))
          ? Number(data.conversationEpoch)
          : epoch ?? 0
        if (epoch !== null && pageEpoch !== epoch) {
          epochChanged = true
          collected.length = 0
        }
        epoch = pageEpoch
        collected.push(
          ...(Array.isArray(data.events) ? data.events : []),
        )
        cursor = typeof data.cursor === 'string' ? data.cursor : cursor
        hasMore = data.hasMore === true
      } while (hasMore)

      cursorRef.current = cursor
      conversationEpochRef.current = epoch
      if (seenRef.current === null || epochChanged) {
        seenRef.current = new Set(collected.map(item => item.eventId))
        setSnapshotSessionId(requestedSessionId)
        setEvents(collected)
        return collected.length > 0 || epochChanged
      }

      const discovered = collected.filter(
        item => !seenRef.current!.has(item.eventId),
      )
      discovered.forEach(item => seenRef.current!.add(item.eventId))
      setSnapshotSessionId(requestedSessionId)
      setEvents(current => {
        if (discovered.length === 0) return current
        return [...current, ...discovered]
      })
      return discovered.length > 0
    } catch {
      return false
    } finally {
      refreshingRef.current = false
    }
  }, [sessionId])

  useEffect(() => {
    seenRef.current = null
    cursorRef.current = null
    conversationEpochRef.current = null
    refreshingRef.current = false
    setSnapshotSessionId(sessionId)
    setEvents([])

    let cancelled = false
    let polling = false
    let timer: number | null = null

    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer)
        timer = null
      }
    }

    const schedule = () => {
      clearTimer()
      if (
        cancelled ||
        !sessionId ||
        !hasPendingDelivery ||
        document.visibilityState !== 'visible'
      ) {
        return
      }
      timer = window.setTimeout(
        () => { void poll() },
        ACTIVE_POLL_INTERVAL_MS,
      )
    }

    const poll = async () => {
      if (cancelled || polling) return
      polling = true
      clearTimer()
      try {
        await refresh()
      } finally {
        polling = false
        schedule()
      }
    }

    const wake = () => {
      if (document.visibilityState !== 'visible') {
        clearTimer()
        return
      }
      void poll()
    }

    // Baseline immediately. After that, poll only while a producer has
    // explicitly reported that a durable delivery is pending.
    void poll()
    document.addEventListener('visibilitychange', wake)

    return () => {
      cancelled = true
      clearTimer()
      document.removeEventListener('visibilitychange', wake)
    }
  }, [
    deliveryVersion,
    hasPendingDelivery,
    refresh,
    refreshVersion,
    sessionId,
  ])

  return {
    events: snapshotSessionId === sessionId ? events : [],
    refresh,
  }
}
