import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api-client'
import type { DelegationJob } from '@/lib/delegation-jobs'

const POLL_INTERVAL_MS = 2000
const DISCOVERY_WINDOW_MS = 15000

export function useDelegationJobs(
  sessionId: string,
  refreshToken = 0,
) {
  const [jobs, setJobs] = useState<DelegationJob[]>([])
  const [hasPendingDelivery, setHasPendingDelivery] = useState(false)
  const [deliveryVersion, setDeliveryVersion] = useState(0)
  const jobsRef = useRef<DelegationJob[]>([])
  const deliveryStatusesRef = useRef<Map<string, string> | null>(null)
  const activeRef = useRef(false)
  const discoveryUntilRef = useRef(0)
  const sessionRef = useRef(sessionId)
  sessionRef.current = sessionId

  const refresh = useCallback(async () => {
    if (!sessionId) return
    const requestedSession = sessionId
    const response = await apiFetch(
      `delegations?session_id=${encodeURIComponent(sessionId)}`,
      { cache: 'no-store' },
    )
    if (!response.ok) return
    const data = await response.json()
    if (sessionRef.current !== requestedSession) return
    const next: DelegationJob[] = Array.isArray(data.jobs) ? data.jobs : []

    const previousDeliveryStatuses = deliveryStatusesRef.current
    if (previousDeliveryStatuses) {
      const deliveryTransition = next.some(job =>
        ['pending', 'published', 'delivered'].includes(job.deliveryStatus) &&
        previousDeliveryStatuses.get(job.jobId) !== job.deliveryStatus,
      )
      if (deliveryTransition) {
        setDeliveryVersion(version => version + 1)
      }
    }
    deliveryStatusesRef.current = new Map(
      next.map(job => [job.jobId, job.deliveryStatus]),
    )
    const pendingDelivery = next.some(job =>
      ['pending', 'published'].includes(job.deliveryStatus),
    )
    setHasPendingDelivery(pendingDelivery)

    jobsRef.current = next
    activeRef.current =
      next.some(job => ['queued', 'running'].includes(job.executionStatus)) ||
      pendingDelivery ||
      Date.now() < discoveryUntilRef.current
    setJobs(next)
  }, [sessionId])

  useEffect(() => {
    jobsRef.current = []
    deliveryStatusesRef.current = null
    activeRef.current = false
    discoveryUntilRef.current = 0
    setJobs([])
    setHasPendingDelivery(false)
    setDeliveryVersion(0)
    void refresh()
    const timer = window.setInterval(() => {
      if (activeRef.current) void refresh()
    }, POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    if (refreshToken <= 0) return
    discoveryUntilRef.current = Date.now() + DISCOVERY_WINDOW_MS
    activeRef.current = true
    void refresh()
  }, [refresh, refreshToken])

  const cancel = useCallback(async (jobId: string) => {
    const response = await apiFetch('delegations', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        job_id: jobId,
      }),
    })
    if (response.ok) await refresh()
    return response.ok
  }, [refresh, sessionId])

  return {
    jobs,
    activeJobs: jobs.filter(job =>
      ['queued', 'running'].includes(job.executionStatus),
    ),
    hasPendingDelivery,
    deliveryVersion,
    cancel,
    refresh,
  }
}
