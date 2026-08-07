import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api-client'
import type { SessionEventProjection } from '@/lib/session-events'

const ACTIVE_POLL_INTERVAL_MS = 2000
const IDLE_POLL_INTERVALS_MS = [5000, 10000, 30000] as const

export function useSessionEvents(sessionId: string, hasPendingDelivery = false) {
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
    let quietPolls = 0
    let timer: number | null = null

    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer)
        timer = null
      }
    }

    const nextIdleDelay = () => {
      const index = Math.min(
        Math.max(quietPolls - 1, 0),
        IDLE_POLL_INTERVALS_MS.length - 1,
      )
      return IDLE_POLL_INTERVALS_MS[index]
    }

    const schedule = () => {
      clearTimer()
      if (
        cancelled ||
        !sessionId ||
        document.visibilityState !== 'visible'
      ) {
        return
      }
      timer = window.setTimeout(
        () => { void poll() },
        hasPendingDelivery ? ACTIVE_POLL_INTERVAL_MS : nextIdleDelay(),
      )
    }

    const poll = async () => {
      if (cancelled || polling) return
      polling = true
      clearTimer()
      try {
        const changed = await refresh()
        if (hasPendingDelivery || changed) {
          quietPolls = 0
        } else {
          quietPolls += 1
        }
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
      quietPolls = 0
      void poll()
    }

    // Baseline the session immediately. Subsequent reads are adaptive.
    void poll()
    document.addEventListener('visibilitychange', wake)
    window.addEventListener('focus', wake)

    return () => {
      cancelled = true
      clearTimer()
      document.removeEventListener('visibilitychange', wake)
      window.removeEventListener('focus', wake)
    }
  }, [hasPendingDelivery, refresh, sessionId])

  return {
    events: snapshotSessionId === sessionId ? events : [],
    refresh,
  }
}
