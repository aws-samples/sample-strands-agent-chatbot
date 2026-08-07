import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api-client'
import type { SessionEventProjection } from '@/lib/session-events'

const ACTIVE_POLL_INTERVAL_MS = 2000

export function useSessionEvents(
  sessionId: string,
  hasPendingDelivery = false,
  deliveryVersion = 0,
) {
  const [events, setEvents] = useState<SessionEventProjection[]>([])
  const [snapshotSessionId, setSnapshotSessionId] = useState(sessionId)
  const seenRef = useRef<Set<string> | null>(null)
  const currentEventIdsRef = useRef<Set<string>>(new Set())
  const refreshingRef = useRef(false)
  const sessionRef = useRef(sessionId)
  sessionRef.current = sessionId

  const refresh = useCallback(async (): Promise<boolean> => {
    if (!sessionId || refreshingRef.current) return false
    const requestedSessionId = sessionId
    refreshingRef.current = true
    try {
      const response = await apiFetch(
        `session/events?session_id=${encodeURIComponent(sessionId)}`,
        { cache: 'no-store' },
      )
      if (!response.ok) return false
      const data = await response.json()
      if (sessionRef.current !== requestedSessionId) return false
      const next: SessionEventProjection[] = Array.isArray(data.events)
        ? data.events
        : []
      const nextIds = new Set(next.map(item => item.eventId))
      const previousIds = currentEventIdsRef.current
      const snapshotChanged =
        nextIds.size !== previousIds.size ||
        [...nextIds].some(eventId => !previousIds.has(eventId))
      currentEventIdsRef.current = nextIds

      if (seenRef.current === null) {
        seenRef.current = new Set(next.map(item => item.eventId))
        // The history request and this first projection read are independent.
        // Let the consumer suppress events already represented by history so
        // a completion committed between the two reads cannot be lost.
        setSnapshotSessionId(requestedSessionId)
        setEvents(next)
        return snapshotChanged
      }

      const discovered = next.filter(item => !seenRef.current!.has(item.eventId))
      discovered.forEach(item => seenRef.current!.add(item.eventId))
      const durableIds = new Set(next.map(item => item.eventId))
      setSnapshotSessionId(requestedSessionId)
      setEvents(current => {
        const retained = current.filter(item => durableIds.has(item.eventId))
        if (
          discovered.length === 0 &&
          retained.length === current.length
        ) {
          return current
        }
        return [...retained, ...discovered]
      })
      return snapshotChanged
    } catch {
      return false
    } finally {
      refreshingRef.current = false
    }
  }, [sessionId])

  useEffect(() => {
    seenRef.current = null
    currentEventIdsRef.current = new Set()
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
  }, [deliveryVersion, hasPendingDelivery, refresh, sessionId])

  return {
    events: snapshotSessionId === sessionId ? events : [],
    refresh,
  }
}
