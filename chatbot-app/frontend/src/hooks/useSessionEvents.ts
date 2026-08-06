import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api-client'
import type { SessionEventProjection } from '@/lib/session-events'

const POLL_INTERVAL_MS = 2000

export function useSessionEvents(sessionId: string) {
  const [events, setEvents] = useState<SessionEventProjection[]>([])
  const seenRef = useRef<Set<string> | null>(null)
  const refreshingRef = useRef(false)
  const sessionRef = useRef(sessionId)
  sessionRef.current = sessionId

  const refresh = useCallback(async () => {
    if (!sessionId || refreshingRef.current) return
    const requestedSessionId = sessionId
    refreshingRef.current = true
    try {
      const response = await apiFetch(
        `session/events?session_id=${encodeURIComponent(sessionId)}`,
        { cache: 'no-store' },
      )
      if (!response.ok) return
      const data = await response.json()
      if (sessionRef.current !== requestedSessionId) return
      const next: SessionEventProjection[] = Array.isArray(data.events)
        ? data.events
        : []

      if (seenRef.current === null) {
        seenRef.current = new Set(next.map(item => item.eventId))
        // The history request and this first projection read are independent.
        // Let the consumer suppress events already represented by history so
        // a completion committed between the two reads cannot be lost.
        setEvents(next)
        return
      }

      const discovered = next.filter(item => !seenRef.current!.has(item.eventId))
      discovered.forEach(item => seenRef.current!.add(item.eventId))
      const durableIds = new Set(next.map(item => item.eventId))
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
    } finally {
      refreshingRef.current = false
    }
  }, [sessionId])

  useEffect(() => {
    seenRef.current = null
    refreshingRef.current = false
    setEvents([])
    void refresh()

    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [refresh])

  return { events, refresh }
}
