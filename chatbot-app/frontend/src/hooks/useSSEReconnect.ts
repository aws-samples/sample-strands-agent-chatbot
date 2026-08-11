import { useCallback, useRef, useState } from 'react'
import { getApiUrl } from '@/config/environment'
import { AGUI_EVENT_TYPES, AGUIStreamEvent } from '@/types/events'

interface ReconnectState {
  executionId: string | null
  isReconnecting: boolean
  reconnectAttempt: number
}

const MAX_ATTEMPTS = 5
const BASE_DELAY_MS = 1000
const MAX_DELAY_MS = 16000
const FETCH_TIMEOUT_MS = 10000
const STORAGE_KEY_PREFIX = 'sse_exec_'

/** Persist executionId to sessionStorage. */
function persistExecutionId(executionId: string) {
  try {
    sessionStorage.setItem(
      `${STORAGE_KEY_PREFIX}${executionId}`,
      JSON.stringify({ executionId, ts: Date.now() })
    )
  } catch { /* quota exceeded or unavailable */ }
}

/** Load persisted executionId for a given session. */
function loadPersistedExecutionId(sessionId: string): string | null {
  try {
    for (let i = 0; i < sessionStorage.length; i++) {
      const key = sessionStorage.key(i)
      if (!key?.startsWith(STORAGE_KEY_PREFIX)) continue
      const raw = sessionStorage.getItem(key)
      if (!raw) continue
      const data = JSON.parse(raw)
      // executionId format: "{sessionId}:{runId}"
      if (data.executionId?.startsWith(sessionId + ':')) {
        // Discard entries older than 10 minutes
        if (Date.now() - data.ts > 10 * 60 * 1000) {
          sessionStorage.removeItem(key)
          continue
        }
        return data.executionId
      }
    }
  } catch { /* unavailable */ }
  return null
}

/** Clear persisted executionId. */
function clearPersistedExecutionId(executionId: string) {
  try {
    sessionStorage.removeItem(`${STORAGE_KEY_PREFIX}${executionId}`)
  } catch { /* unavailable */ }
}

export function useSSEReconnect() {
  const stateRef = useRef<ReconnectState>({
    executionId: null,
    isReconnecting: false,
    reconnectAttempt: 0,
  })
  const reconnectGenerationRef = useRef(0)
  const activeControllerRef = useRef<AbortController | null>(null)
  const [isReconnecting, setIsReconnecting] = useState(false)
  const [reconnectAttempt, setReconnectAttempt] = useState(0)

  const onStreamStart = useCallback((executionId: string) => {
    stateRef.current = {
      executionId,
      isReconnecting: false,
      reconnectAttempt: 0,
    }
    persistExecutionId(executionId)
    setIsReconnecting(false)
    setReconnectAttempt(0)
  }, [])

  const detach = useCallback(() => {
    reconnectGenerationRef.current += 1
    activeControllerRef.current?.abort()
    activeControllerRef.current = null
    stateRef.current = {
      ...stateRef.current,
      isReconnecting: false,
      reconnectAttempt: 0,
    }
    setIsReconnecting(false)
    setReconnectAttempt(0)
  }, [])

  const reset = useCallback(() => {
    detach()
    if (stateRef.current.executionId) {
      clearPersistedExecutionId(stateRef.current.executionId)
    }
    stateRef.current = {
      executionId: null,
      isReconnecting: false,
      reconnectAttempt: 0,
    }
    setIsReconnecting(false)
    setReconnectAttempt(0)
  }, [detach])

  /** Restore execution state from sessionStorage (for page refresh). */
  const restoreFromSession = useCallback((sessionId: string): boolean => {
    detach()
    const executionId = loadPersistedExecutionId(sessionId)
    stateRef.current = {
      executionId,
      isReconnecting: false,
      reconnectAttempt: 0,
    }
    return executionId !== null
  }, [detach])

  const attemptReconnect = useCallback(async (
    onEvent: (event: AGUIStreamEvent) => void,
    onComplete: () => void,
    onFail: () => void,
    getAuthHeaders: () => Promise<Record<string, string>>,
    onConnected?: () => void,
  ) => {
    const { executionId } = stateRef.current
    if (!executionId) {
      onFail()
      return
    }

    // Prevent concurrent reconnect attempts
    if (stateRef.current.isReconnecting) {
      console.log('[SSEReconnect] Already reconnecting, skipping duplicate attempt')
      return
    }

    stateRef.current.isReconnecting = true
    setIsReconnecting(true)

    const reconnectGeneration = reconnectGenerationRef.current + 1
    reconnectGenerationRef.current = reconnectGeneration
    let connectedFired = false

    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      if (
        reconnectGenerationRef.current !== reconnectGeneration
        || stateRef.current.executionId !== executionId
      ) {
        return
      }
      stateRef.current.reconnectAttempt = attempt + 1
      stateRef.current.isReconnecting = true
      setReconnectAttempt(attempt + 1)
      setIsReconnecting(true)

      // Exponential backoff with jitter
      if (attempt > 0) {
        const baseDelay = Math.min(BASE_DELAY_MS * Math.pow(2, attempt - 1), MAX_DELAY_MS)
        const delay = Math.floor(baseDelay * (0.5 + crypto.getRandomValues(new Uint32Array(1))[0] / 0x100000000 * 0.5)) // lgtm[js/biased-cryptographic-random]
        await new Promise(resolve => setTimeout(resolve, delay))
        if (
          reconnectGenerationRef.current !== reconnectGeneration
          || stateRef.current.executionId !== executionId
        ) {
          return
        }
      }

      try {
        // 1. Check execution status via BFF buffer
        const headers = await getAuthHeaders()
        if (
          reconnectGenerationRef.current !== reconnectGeneration
          || stateRef.current.executionId !== executionId
        ) {
          return
        }
        const statusController = new AbortController()
        activeControllerRef.current = statusController
        const statusTimeout = setTimeout(() => statusController.abort(), FETCH_TIMEOUT_MS)
        let statusData: { status: string }
        try {
          const statusUrl = `${getApiUrl('stream/execution-status')}?executionId=${encodeURIComponent(executionId)}`
          const statusRes = await fetch(statusUrl, {
            headers,
            signal: statusController.signal,
          })
          statusData = await statusRes.json()
        } finally {
          clearTimeout(statusTimeout)
          if (activeControllerRef.current === statusController) {
            activeControllerRef.current = null
          }
        }
        if (
          reconnectGenerationRef.current !== reconnectGeneration
          || stateRef.current.executionId !== executionId
        ) {
          return
        }

        if (statusData.status === 'not_found') {
          console.log('[SSEReconnect] Execution not found, falling back to history')
          break
        }

        // 2. Resume SSE stream from cursor=0 (full replay from BFF buffer)
        const resumeController = new AbortController()
        activeControllerRef.current = resumeController
        const resumeTimeout = setTimeout(() => resumeController.abort(), FETCH_TIMEOUT_MS)
        const resumeUrl = `${getApiUrl('stream/resume')}?executionId=${encodeURIComponent(executionId)}&cursor=0`
        let response: Response
        try {
          response = await fetch(resumeUrl, {
            headers: { ...headers, 'Accept': 'text/event-stream' },
            signal: resumeController.signal,
          })
        } finally {
          clearTimeout(resumeTimeout)
        }

        if (!response.ok) {
          if (activeControllerRef.current === resumeController) {
            activeControllerRef.current = null
          }
          console.warn(`[SSEReconnect] Resume failed with ${response.status}, attempt ${attempt + 1}`)
          continue
        }

        if (!response.body) {
          if (activeControllerRef.current === resumeController) {
            activeControllerRef.current = null
          }
          console.warn('[SSEReconnect] No body in resume response')
          continue
        }

        // 3. Parse SSE stream (full replay)
        console.log(`[SSEReconnect] Resumed from cursor 0 (full replay)`)
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let currentEventId: number | null = null

        try {
          while (true) {
            const { done, value } = await reader.read()
            if (done) break
            if (
              reconnectGenerationRef.current !== reconnectGeneration
              || stateRef.current.executionId !== executionId
            ) {
              await reader.cancel()
              return
            }

            buffer += decoder.decode(value, { stream: true })
            const lines = buffer.split('\n')
            buffer = lines.pop() || ''

            for (const line of lines) {
              if (line.startsWith('id: ')) {
                currentEventId = parseInt(line.substring(4), 10)
                continue
              }
              if (line.startsWith('data: ')) {
                try {
                  const eventData = JSON.parse(line.substring(6))
                  // Skip internal metadata events
                  if (eventData.type === 'CUSTOM' && eventData.name === 'execution_meta') {
                    currentEventId = null
                    continue
                  }
                  const embeddedEventId = Number(eventData.eventId)
                  const eventId = currentEventId && currentEventId > 0
                    ? currentEventId
                    : embeddedEventId > 0
                      ? embeddedEventId
                      : null
                  if (eventId !== null) {
                    eventData._eventId = eventId
                  }
                  eventData._executionId = executionId
                  currentEventId = null
                  // Dispatch event
                  if (eventData.type && AGUI_EVENT_TYPES.includes(eventData.type)) {
                    onEvent(eventData as AGUIStreamEvent)
                    // Clear reconnecting badge on first real event
                    if (!connectedFired) {
                      connectedFired = true
                      setIsReconnecting(false)
                      setReconnectAttempt(0)
                      onConnected?.()
                    }
                  }
                } catch {
                  // Skip unparseable lines
                }
              }
            }
          }
        } finally {
          reader.releaseLock()
          if (activeControllerRef.current === resumeController) {
            activeControllerRef.current = null
          }
        }

        if (
          reconnectGenerationRef.current !== reconnectGeneration
          || stateRef.current.executionId !== executionId
        ) {
          return
        }
        // Success — clear persisted executionId
        clearPersistedExecutionId(executionId)
        stateRef.current.isReconnecting = false
        stateRef.current.reconnectAttempt = 0
        setIsReconnecting(false)
        setReconnectAttempt(0)
        onComplete()
        return
      } catch (error) {
        if (
          reconnectGenerationRef.current !== reconnectGeneration
          || stateRef.current.executionId !== executionId
        ) {
          return
        }
        console.warn(`[SSEReconnect] Attempt ${attempt + 1} failed:`, error)
        continue
      }
    }

    // All attempts exhausted
    clearPersistedExecutionId(executionId)
    stateRef.current.isReconnecting = false
    setIsReconnecting(false)
    setReconnectAttempt(0)
    onFail()
  }, [])

  return {
    onStreamStart,
    attemptReconnect,
    restoreFromSession,
    reset,
    detach,
    isReconnecting,
    reconnectAttempt,
  }
}
