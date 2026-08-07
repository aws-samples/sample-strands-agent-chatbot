import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api-client'
import type { ResearchJob } from '@/lib/research-jobs'

const POLL_INTERVAL_MS = 2000
const DISCOVERY_WINDOW_MS = 15000

export function useResearchJobs(sessionId: string, refreshToken = 0) {
  const [jobs, setJobs] = useState<ResearchJob[]>([])
  const [deliveredJobIds, setDeliveredJobIds] = useState<string[]>([])
  const [isActive, setIsActive] = useState(false)
  const [hasPendingDelivery, setHasPendingDelivery] = useState(false)
  const [deliveryVersion, setDeliveryVersion] = useState(0)
  const [snapshotSessionId, setSnapshotSessionId] = useState(sessionId)
  const statusesRef = useRef<Map<string, string> | null>(null)
  const jobsRef = useRef<ResearchJob[]>([])
  const refreshingRef = useRef(false)
  const pendingRefreshRef = useRef(false)
  const activeRef = useRef(false)
  const discoveryUntilRef = useRef(0)
  const invocationCountRef = useRef(0)
  const refreshTokenRef = useRef(refreshToken)
  const sessionRef = useRef(sessionId)
  sessionRef.current = sessionId
  refreshTokenRef.current = refreshToken

  const refresh = useCallback(async () => {
    if (!sessionId) return
    if (refreshingRef.current) {
      pendingRefreshRef.current = true
      return
    }
    const requestedSessionId = sessionId
    refreshingRef.current = true
    try {
      const response = await apiFetch(`research/jobs?session_id=${encodeURIComponent(sessionId)}`, {
        cache: 'no-store',
      })
      if (!response.ok) return
      const data = await response.json()
      if (sessionRef.current !== requestedSessionId) return
      let nextJobs: ResearchJob[] = Array.isArray(data.jobs) ? data.jobs : []
      const existingById = new Map(jobsRef.current.map(job => [job.jobId, job]))
      nextJobs = nextJobs.map(job => {
        const existing = existingById.get(job.jobId)
        return existing?.artifact?.content
          ? { ...job, artifact: existing.artifact }
          : job
      })

      const needsContent = nextJobs.some(job =>
        ['completed', 'delivering', 'delivered'].includes(job.status) &&
        !job.artifact?.content,
      )
      if (needsContent) {
        const contentResponse = await apiFetch(
          `research/jobs?session_id=${encodeURIComponent(sessionId)}&include_content=true`,
          { cache: 'no-store' },
        )
        if (contentResponse.ok) {
          const contentData = await contentResponse.json()
          if (sessionRef.current !== requestedSessionId) return
          const hydrated: ResearchJob[] = Array.isArray(contentData.jobs) ? contentData.jobs : []
          const hydratedById = new Map(hydrated.map(job => [job.jobId, job]))
          nextJobs = nextJobs.map(job => hydratedById.get(job.jobId) || job)
        }
      }

      const previous = statusesRef.current
      if (previous) {
        const transitions = nextJobs
          .filter(job => job.status === 'delivered' && previous.get(job.jobId) !== 'delivered')
          .map(job => job.jobId)
        setDeliveredJobIds(transitions)
        const deliveryTransitions = nextJobs.some(job =>
          ['delivering', 'delivered'].includes(job.status) &&
          previous.get(job.jobId) !== job.status,
        )
        if (deliveryTransitions) setDeliveryVersion(version => version + 1)
      }
      statusesRef.current = new Map(nextJobs.map(job => [job.jobId, job.status]))
      setHasPendingDelivery(nextJobs.some(job => job.status === 'delivering'))
      const hasActiveJobs = nextJobs.some(job =>
        ['queued', 'running', 'delivering'].includes(job.status),
      )
      const expectedJobs = invocationCountRef.current
      if (nextJobs.length >= expectedJobs) {
        discoveryUntilRef.current = 0
      }
      const waitingForReceipt =
        nextJobs.length < expectedJobs &&
        Date.now() < discoveryUntilRef.current
      const nextActive = hasActiveJobs || waitingForReceipt
      activeRef.current = nextActive
      setIsActive(nextActive)
      const changed = nextJobs.length !== jobsRef.current.length || nextJobs.some((job, index) => {
        const current = jobsRef.current[index]
        return !current ||
          current.jobId !== job.jobId ||
          current.status !== job.status ||
          current.updatedAt !== job.updatedAt ||
          current.artifact?.content !== job.artifact?.content
      })
      jobsRef.current = nextJobs
      if (changed) {
        setSnapshotSessionId(requestedSessionId)
        setJobs(nextJobs)
      }
    } finally {
      refreshingRef.current = false
      if (pendingRefreshRef.current && sessionRef.current === requestedSessionId) {
        pendingRefreshRef.current = false
        void refresh()
      }
    }
  }, [sessionId])

  useEffect(() => {
    statusesRef.current = null
    jobsRef.current = []
    activeRef.current = false
    discoveryUntilRef.current = 0
    // Tool invocations already present when a session opens are history, not
    // evidence that a new job receipt is still propagating.
    invocationCountRef.current = refreshTokenRef.current
    refreshingRef.current = false
    pendingRefreshRef.current = false
    setSnapshotSessionId(sessionId)
    setJobs([])
    setDeliveredJobIds([])
    setIsActive(false)
    setHasPendingDelivery(false)
    setDeliveryVersion(0)
    void refresh()
    const timer = window.setInterval(() => {
      if (activeRef.current) void refresh()
    }, POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    if (refreshToken <= invocationCountRef.current) return
    invocationCountRef.current = refreshToken
    discoveryUntilRef.current = Date.now() + DISCOVERY_WINDOW_MS
    activeRef.current = true
    setIsActive(true)
    void refresh()
  }, [refresh, refreshToken])

  return {
    jobs: snapshotSessionId === sessionId ? jobs : [],
    deliveredJobIds:
      snapshotSessionId === sessionId ? deliveredJobIds : [],
    isActive: snapshotSessionId === sessionId && isActive,
    hasPendingDelivery:
      snapshotSessionId === sessionId && hasPendingDelivery,
    deliveryVersion:
      snapshotSessionId === sessionId ? deliveryVersion : 0,
    refresh,
  }
}
