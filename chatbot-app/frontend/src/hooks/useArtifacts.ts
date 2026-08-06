import { useState, useEffect, useCallback, useRef } from 'react'
import { Artifact } from '@/types/artifact'

interface ArtifactSessionState {
  sessionId: string
  artifacts: Artifact[]
  selectedArtifactId: string | null
  loadedFromBackend: boolean
}

/**
 * Convert backend artifact format to frontend Artifact.
 */
function toFrontendArtifact(item: any, sessionId: string): Artifact {
  let timestamp = item.timestamp || item.created_at
  if (timestamp) {
    try {
      const date = new Date(timestamp)
      timestamp = !isNaN(date.getTime()) ? date.toISOString() : new Date().toISOString()
    } catch {
      timestamp = new Date().toISOString()
    }
  } else {
    timestamp = new Date().toISOString()
  }

  return {
    id: item.id,
    type: item.type,
    title: item.title,
    content: item.content,
    description: item.metadata?.description || item.description || '',
    toolName: item.tool_name || item.toolName,
    timestamp,
    sessionId,
    metadata: item.metadata,
  }
}

/**
 * Read artifacts from sessionStorage for the given session.
 */
function readStorageArtifacts(sessionId: string): Artifact[] {
  const stored = sessionStorage.getItem(`artifacts-${sessionId}`)
  if (!stored) return []
  try {
    const data = JSON.parse(stored)
    if (!Array.isArray(data)) return []
    return data.map((item: any) => toFrontendArtifact(item, sessionId))
  } catch {
    return []
  }
}

/**
 * Custom hook for managing artifacts loaded from agent state (backend).
 * Artifacts are stored in agent.state by tools and persisted via session manager.
 *
 * Single source of truth: React state (artifacts).
 * sessionStorage is kept in sync via useEffect for persistence across reloads.
 */
export function useArtifacts(
  sessionId: string
) {
  const [sessionState, setSessionState] = useState<ArtifactSessionState>({
    sessionId,
    artifacts: [],
    selectedArtifactId: null,
    loadedFromBackend: false,
  })
  const [isCanvasOpen, setIsCanvasOpen] = useState<boolean>(false)
  const [justUpdated, setJustUpdated] = useState<boolean>(false)
  const activeSessionIdRef = useRef(sessionId)
  activeSessionIdRef.current = sessionId

  // Never expose a snapshot owned by the previous session, even during the
  // render before the session initialization effect runs.
  const isCurrentSession = sessionState.sessionId === sessionId
  const artifacts = isCurrentSession ? sessionState.artifacts : []
  const selectedArtifactId = isCurrentSession
    ? sessionState.selectedArtifactId
    : null
  const loadedFromBackend =
    isCurrentSession && sessionState.loadedFromBackend

  // Load artifacts from sessionStorage on session init (populated by history API)
  useEffect(() => {
    const loaded = readStorageArtifacts(sessionId)
    setSessionState({
      sessionId,
      artifacts: loaded,
      selectedArtifactId: null,
      loadedFromBackend: true,
    })
  }, [sessionId])

  // Auto-sync only the snapshot owned by the active session.
  useEffect(() => {
    if (!loadedFromBackend) return
    sessionStorage.setItem(`artifacts-${sessionId}`, JSON.stringify(artifacts))
  }, [sessionId, loadedFromBackend, artifacts])

  const toggleCanvas = useCallback(() => {
    setIsCanvasOpen(prev => !prev)
  }, [])

  const openCanvas = useCallback(() => {
    setIsCanvasOpen(true)
  }, [])

  const openArtifact = useCallback((id: string) => {
    setSessionState(current => current.sessionId === sessionId
      ? { ...current, selectedArtifactId: id }
      : current
    )
    setIsCanvasOpen(true)
  }, [sessionId])

  const closeCanvas = useCallback(() => {
    setIsCanvasOpen(false)
    setSessionState(current => current.sessionId === sessionId
      ? { ...current, selectedArtifactId: null }
      : current
    )
  }, [sessionId])

  const addArtifact = useCallback((artifact: Artifact) => {
    setSessionState(current => {
      if (current.sessionId !== sessionId) return current
      const existingIndex = current.artifacts.findIndex(a => a.id === artifact.id)
      if (existingIndex >= 0) {
        return {
          ...current,
          artifacts: current.artifacts.map((a, i) =>
            i === existingIndex ? artifact : a
          ),
        }
      }
      return { ...current, artifacts: [...current.artifacts, artifact] }
    })
  }, [sessionId])

  const removeArtifact = useCallback((artifactId: string) => {
    setSessionState(current => {
      if (current.sessionId !== sessionId) return current
      return {
        ...current,
        artifacts: current.artifacts.filter(a => a.id !== artifactId),
        selectedArtifactId:
          current.selectedArtifactId === artifactId
            ? null
            : current.selectedArtifactId,
      }
    })
  }, [sessionId])

  const updateArtifact = useCallback((artifactId: string, updates: Partial<Artifact>) => {
    setSessionState(current => current.sessionId === sessionId
      ? {
          ...current,
          artifacts: current.artifacts.map(a =>
            a.id === artifactId ? { ...a, ...updates } : a
          ),
        }
      : current
    )
  }, [sessionId])

  /**
   * Refresh artifacts from history API.
   * Returns the refreshed artifacts array for immediate use.
   */
  const refreshArtifacts = useCallback(async (options?: { skipFlashEffect?: boolean }): Promise<Artifact[]> => {
    const requestedSessionId = sessionId
    try {
      const response = await fetch(`/api/conversation/history?session_id=${sessionId}`)
      if (response.ok) {
        const data = await response.json()
        const artifactsData = data.artifacts || []
        const converted = Array.isArray(artifactsData)
          ? artifactsData.map((item: any) =>
              toFrontendArtifact(item, requestedSessionId)
            )
          : []
        if (activeSessionIdRef.current !== requestedSessionId) return []
        setSessionState(current => current.sessionId === requestedSessionId
          ? { ...current, artifacts: converted, loadedFromBackend: true }
          : current
        )

        if (!options?.skipFlashEffect) {
          setJustUpdated(true)
          setTimeout(() => setJustUpdated(false), 1500)
        }
        return converted
      }
    } catch (error) {
      console.error('[useArtifacts] Failed to refresh artifacts:', error)
    }
    return []
  }, [sessionId])

  /**
   * Re-read artifacts from sessionStorage.
   * Called after loadSession populates sessionStorage.
   */
  const reloadFromStorage = useCallback(() => {
    const loaded = readStorageArtifacts(sessionId)
    if (activeSessionIdRef.current !== sessionId) return
    setSessionState({
      sessionId,
      artifacts: loaded,
      selectedArtifactId: null,
      loadedFromBackend: true,
    })
  }, [sessionId])

  const setSelectedArtifactId = useCallback((id: string | null) => {
    setSessionState(current => current.sessionId === sessionId
      ? { ...current, selectedArtifactId: id }
      : current
    )
  }, [sessionId])

  return {
    artifacts,
    selectedArtifactId,
    isCanvasOpen,
    toggleCanvas,
    openCanvas,
    openArtifact,
    closeCanvas,
    setSelectedArtifactId,
    addArtifact,
    removeArtifact,
    updateArtifact,
    refreshArtifacts,
    reloadFromStorage,
    justUpdated,
  }
}
