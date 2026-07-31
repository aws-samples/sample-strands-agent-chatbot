import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { Message, ToolExecution } from '@/types/chat'
import { ReasoningState, ChatSessionState, ChatUIState, InterruptState, AgentStatus, PendingOAuthState } from '@/types/events'
import { detectBackendUrl } from '@/utils/chat'
import { useStreamEvents } from './useStreamEvents'
import { useChatAPI, SessionPreferences } from './useChatAPI'
import { usePolling, hasOngoingA2ATools, A2A_TOOLS_REQUIRING_POLLING } from './usePolling'
import { getApiUrl } from '@/config/environment'
import { generateSessionId } from '@/config/session'
import { fetchAuthSession } from 'aws-amplify/auth'
import { apiGet, apiPost } from '@/lib/api-client'

import { WorkspaceDocument } from './useStreamEvents'
import { ExtractedDataInfo } from './useCanvasHandlers'
import { DocumentType } from '@/config/document-tools'

interface UseChatProps {
  onSessionCreated?: () => void
  onArtifactUpdated?: () => void  // Callback when artifact is updated via update_artifact tool
  onWordDocumentsCreated?: (documents: WorkspaceDocument[]) => void  // Callback when Word documents are created
  onExcelDocumentsCreated?: (documents: WorkspaceDocument[]) => void  // Callback when Excel documents are created
  onPptDocumentsCreated?: (documents: WorkspaceDocument[]) => void  // Callback when PowerPoint documents are created
  onDiagramCreated?: (s3Key: string, filename: string) => void  // Callback when diagram is generated
  onBrowserSessionDetected?: (browserSessionId: string, browserId: string) => void  // Callback when browser session is first detected
  onExtractedDataCreated?: (data: ExtractedDataInfo) => void  // Callback when browser_extract creates artifact
  onExcalidrawCreated?: (data: { elements: any[]; appState: any; title: string }, toolUseId: string) => void  // Callback when excalidraw diagram is created
  onSessionLoaded?: () => void  // Callback when session load completes (artifacts ready in sessionStorage)
}

interface UseChatReturn {
  messages: Message[]
  groupedMessages: Array<{
    type: 'user' | 'assistant_turn'
    messages: Message[]
    id: string
  }>
  isConnected: boolean
  isTyping: boolean
  agentStatus: AgentStatus
  currentToolExecutions: ToolExecution[]
  currentReasoning: ReasoningState | null
  showProgressPanel: boolean
  toggleProgressPanel: () => void
  sendMessage: (text: string, files?: File[], systemPrompt?: string, selectedArtifactId?: string | null) => Promise<void>
  stopGeneration: () => Promise<void>
  newChat: () => Promise<void>
  compactSession: () => Promise<void>
  truncateFromMessage: (message: Message) => Promise<void>
  sessionId: string
  isLoadingMessages: boolean
  isCompacting: boolean
  loadSession: (sessionId: string) => Promise<void>
  browserSession: { sessionId: string | null; browserId: string | null } | null
  browserProgress?: Array<{ stepNumber: number; content: string }>
  researchProgress?: { stepNumber: number; content: string }
  codeProgress?: Array<{ stepNumber: number; content: string }>
  respondToInterrupt: (interruptId: string, response: string) => Promise<void>
  currentInterrupt: InterruptState | null
  // Per-session model state
  currentModelId: string
  currentTemperature: number
  updateModelConfig: (modelId: string, temperature?: number) => void
  // Legacy swarm progress — kept as an optional passthrough from session state
  // so history playback still renders old SwarmProgress panels. Swarm mode
  // itself has been removed from the product.
  swarmProgress?: {
    isActive: boolean
    currentNode: string
    currentNodeDescription: string
    nodeHistory: string[]
    status: 'idle' | 'running' | 'completed' | 'failed'
  }
  // Voice mode
  addVoiceToolExecution: (toolExecution: ToolExecution) => void
  updateVoiceMessage: (role: 'user' | 'assistant', text: string, isFinal: boolean) => void
  setVoiceStatus: (status: AgentStatus) => void
  finalizeVoiceMessage: () => void
  // Artifact message
  addArtifactMessage: (artifact: { id: string; type: string; title: string; wordCount?: number }) => void
  // OAuth state
  pendingOAuth: PendingOAuthState | null | undefined
  cancelOAuth: () => void
  // SSE reconnection state
  isReconnecting: boolean
  reconnectAttempt: number
}

// Default preferences when session has no saved preferences
const DEFAULT_PREFERENCES: SessionPreferences = {
  lastModel: 'openai.gpt-5.6-terra',
  selectedPromptId: 'general',
}

export const useChat = (props?: UseChatProps): UseChatReturn => {
  // ==================== STATE ====================
  const [messages, setMessages] = useState<Message[]>([])
  const [backendUrl, setBackendUrl] = useState('http://localhost:8000')
  const [sessionId, setSessionId] = useState<string>(() => {
    if (typeof window === 'undefined') return generateSessionId()
    const saved = sessionStorage.getItem('chat-session-id')
    if (saved) return saved
    const newId = generateSessionId()
    sessionStorage.setItem('chat-session-id', newId)
    return newId
  })
  const [isLoadingMessages, setIsLoadingMessages] = useState(false)
  // Track which session is being compacted; isCompacting is true only when viewing that session
  const [compactingSessionId, setCompactingSessionId] = useState<string | null>(null)

  // Per-session model state (not written to global profile on session switch)
  const [currentModelId, setCurrentModelId] = useState(DEFAULT_PREFERENCES.lastModel!)
  const [currentTemperature, setCurrentTemperature] = useState(0.5)


  // Ref for onSessionLoaded callback to avoid stale closure in useCallback
  const onSessionLoadedRef = useRef(props?.onSessionLoaded)
  onSessionLoadedRef.current = props?.onSessionLoaded

  const [sessionState, setSessionState] = useState<ChatSessionState>({
    reasoning: null,
    streaming: null,
    toolExecutions: [],
    browserSession: null,
    interrupt: null,
    pendingOAuth: null
  })

  const [uiState, setUIState] = useState<ChatUIState>({
    isConnected: true,
    isTyping: false,
    showProgressPanel: false,
    agentStatus: 'idle',
    latencyMetrics: {
      requestStartTime: null,
      timeToFirstToken: null,
      endToEndLatency: null
    }
  })

  // ==================== REFS ====================
  const currentToolExecutionsRef = useRef<ToolExecution[]>([])
  const currentTurnIdRef = useRef<string | null>(null)
  const currentSessionIdRef = useRef<string | null>(null)
  const messagesRef = useRef<Message[]>([])

  // Keep refs in sync with state
  useEffect(() => {
    currentToolExecutionsRef.current = sessionState.toolExecutions
  }, [sessionState.toolExecutions])

  useEffect(() => {
    currentSessionIdRef.current = sessionId
  }, [sessionId])

  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  // ==================== BACKEND DETECTION ====================
  useEffect(() => {
    const initBackend = async () => {
      const { url, connected } = await detectBackendUrl()
      setBackendUrl(url)
      setUIState(prev => ({ ...prev, isConnected: connected }))
    }
    initBackend()
  }, [])

  // ==================== SESSION CREATED CALLBACK ====================
  const handleSessionCreated = useCallback(() => {
    if (typeof (window as any).__refreshSessionList === 'function') {
      (window as any).__refreshSessionList()
    }
    props?.onSessionCreated?.()
  }, [props])

  // ==================== POLLING HOOK ====================
  // Note: Initialize polling first, then pass startPolling to useStreamEvents
  const startPollingRef = useRef<((sessionId: string) => void) | null>(null)
  const stopPollingRef = useRef<(() => void) | null>(null)

  // Track doc types from user file uploads so workspace is refreshed at RUN_FINISHED
  const uploadedDocTypesRef = useRef<Set<DocumentType>>(new Set())

  // ==================== STREAM EVENTS HOOK ====================
  const { handleStreamEvent, resetStreamingState } = useStreamEvents({
    sessionState,
    setSessionState,
    setMessages,
    setUIState,
    uiState,
    currentToolExecutionsRef,
    currentTurnIdRef,
    startPollingRef,
    stopPollingRef,
    sessionId,
    onArtifactUpdated: props?.onArtifactUpdated,
    onWordDocumentsCreated: props?.onWordDocumentsCreated,
    onExcelDocumentsCreated: props?.onExcelDocumentsCreated,
    onPptDocumentsCreated: props?.onPptDocumentsCreated,
    onDiagramCreated: props?.onDiagramCreated,
    onBrowserSessionDetected: props?.onBrowserSessionDetected,
    onExtractedDataCreated: props?.onExtractedDataCreated,
    onExcalidrawCreated: props?.onExcalidrawCreated,
    uploadedDocTypesRef
  })

  // ==================== CHAT API HOOK ====================
  const {
    newChat: apiNewChat,
    compactSession: apiCompactSession,
    truncateSession: apiTruncateSession,
    summarizeForCompact: apiSummarizeForCompact,
    listSessionEvents: apiListSessionEvents,
    sendMessage: apiSendMessage,
    cleanup,
    sendStopSignal,
    loadSession: apiLoadSession,
    isReconnecting,
    reconnectAttempt,
  } = useChatAPI({
    backendUrl,
    setUIState,
    setMessages,
    handleStreamEvent,
    resetStreamingState,
    sessionId,
    setSessionId,
    onSessionCreated: handleSessionCreated,
    currentModelId,
    currentTemperature,
  })

  // Initialize polling with apiLoadSession (now available)
  const { startPolling, stopPolling, checkAndStartPollingForA2ATools } = usePolling({
    sessionId,
    loadSession: apiLoadSession
  })

  // Update polling refs so useStreamEvents can use them
  useEffect(() => {
    startPollingRef.current = startPolling
    stopPollingRef.current = stopPolling
  }, [startPolling, stopPolling])

  // ==================== A2A AGENT UI STATE MANAGEMENT ====================
  // Update UI status based on ongoing A2A agents (research)
  // This is the ONLY place that sets researching status from messages
  // PERFORMANCE: Only check last 5 messages for ongoing tools (recent activity)
  useEffect(() => {
    if (!sessionId || currentSessionIdRef.current !== sessionId) return

    // PERFORMANCE: Only check recent messages (last 5) for ongoing A2A agents
    // Ongoing agents are always in the most recent messages
    let hasOngoingResearch = false

    const startIdx = Math.max(0, messages.length - 5)
    for (let i = messages.length - 1; i >= startIdx; i--) {
      const toolExecutions = messages[i].toolExecutions
      if (!toolExecutions) continue

      for (const te of toolExecutions) {
        if (te.isComplete || te.isCancelled) continue
        if (te.toolName === 'research_agent') hasOngoingResearch = true
      }
      if (hasOngoingResearch) break
    }

    if (hasOngoingResearch) {
      setUIState(prev => {
        if (prev.agentStatus !== 'researching') {
          console.log('[useChat] Setting status to researching')
          return { ...prev, isTyping: true, agentStatus: 'researching' }
        }
        return prev
      })
    } else {
      // No ongoing A2A tools - transition to idle if currently stuck in A2A status.
      // This handles the case where SSE stream dropped (disconnect, session switch)
      // but the A2A agent completed in the background. Without this, agentStatus
      // would stay 'researching' forever since only stream
      // event handlers (complete/error) used to set idle.
      setUIState(prev => {
        if (prev.agentStatus === 'researching') {
          console.log('[useChat] A2A tools completed, transitioning to idle')
          return { ...prev, isTyping: false, agentStatus: 'idle' }
        }
        return prev
      })
      // Stop polling since A2A tools are no longer ongoing
      stopPolling()
    }
  }, [messages, sessionId, stopPolling])

  // ==================== SESSION LOADING ====================
  const loadSessionWithPreferences = useCallback(async (newSessionId: string) => {
    // Immediately update session ref to prevent race conditions
    currentSessionIdRef.current = newSessionId

    // Stop any existing polling
    stopPolling()

    // Set loading state for UI feedback
    setIsLoadingMessages(true)

    // Reset UI and session state — preserve 'compacting' if compact is in progress for this session
    // Check localStorage directly since agentStatus may have been reset when switching sessions
    const hasPendingCompact = !!localStorage.getItem(`compact_pending_${newSessionId}`)
    if (hasPendingCompact) setCompactingSessionId(newSessionId)
    setUIState(prev => ({
      ...prev,
      isTyping: hasPendingCompact,
      agentStatus: hasPendingCompact ? 'compacting' : 'idle',
      showProgressPanel: false
    }))

    setSessionState({
      reasoning: null,
      streaming: null,
      toolExecutions: [],
      browserSession: null,
      browserProgress: undefined,
      researchProgress: undefined,
      interrupt: null,
      pendingOAuth: null
    })

    try {
      const { preferences, messages: loadedMessages } = await apiLoadSession(newSessionId)

    // Verify session hasn't changed during async load
    if (currentSessionIdRef.current !== newSessionId) {
      console.log(`[useChat] Session changed during load, aborting setup`)
      return
    }

    // Use loadedMessages directly to avoid stale messagesRef.current (React render not guaranteed yet)
    checkAndStartPollingForA2ATools(loadedMessages, newSessionId)

    // Merge saved preferences with defaults
    const effectivePreferences: SessionPreferences = {
      ...DEFAULT_PREFERENCES,
      ...preferences,
      lastModel: preferences?.lastModel || DEFAULT_PREFERENCES.lastModel,
    }

    console.log(`[useChat] ${preferences ? 'Restoring session' : 'Using default'} preferences:`, effectivePreferences)

    // Restore model configuration with validation against available models
    let restoredModel = effectivePreferences.lastModel!
    try {
      const modelsResponse = await apiGet<{ models: { id: string }[] }>('model/available-models')
      const validModelIds = modelsResponse.models?.map(m => m.id) || []
      if (validModelIds.length > 0 && !validModelIds.includes(restoredModel)) {
        console.warn(`[useChat] Saved model ${restoredModel} not in available models, falling back to default`)
        restoredModel = DEFAULT_PREFERENCES.lastModel!
      }
    } catch {
      // If fetch fails, use saved model as-is
    }
    setCurrentModelId(restoredModel)
    console.log(`[useChat] Model state updated: ${restoredModel}`)

    // Notify that session loading is complete (artifacts are in sessionStorage)
    onSessionLoadedRef.current?.()
    } finally {
      setIsLoadingMessages(false)
    }
  }, [apiLoadSession, setUIState, setSessionState, stopPolling, checkAndStartPollingForA2ATools])

  // ==================== INITIALIZATION EFFECTS ====================
  // Restore last session on page load
  useEffect(() => {
    const lastSessionId = sessionStorage.getItem('chat-session-id')
    if (lastSessionId) {
      loadSessionWithPreferences(lastSessionId).catch(() => {
        sessionStorage.removeItem('chat-session-id')
        setMessages([])
      })
    } else {
      setMessages([])
    }
  }, [])

  // Clear browserSession when switching sessions (streaming-only state, derived from artifact metadata on restore)
  useEffect(() => {
    setSessionState(prev => ({ ...prev, browserSession: null }))
  }, [sessionId])

  // ==================== OAUTH COMPLETION LISTENER ====================
  // Listen for postMessage from OAuth popup window (or popup-close fallback)
  const oauthSignalledRef = useRef<string | null>(null)
  useEffect(() => {
    const handleOAuthMessage = async (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return
      if (event.data?.type !== 'oauth_elicitation_complete') return

      const elicitationId = sessionState.pendingOAuth?.elicitationId
      if (!elicitationId || !sessionState.pendingOAuth) return

      // Deduplicate: only signal once per elicitation (postMessage + popup-close can both fire)
      if (oauthSignalledRef.current === elicitationId) return
      oauthSignalledRef.current = elicitationId

      console.log('[useChat] OAuth elicitation completion signal:', event.data)

      try {
        // BFF writes the completion signal directly to DynamoDB; no auth
        // header needed. The orchestrator's elicitation_bridge polls DDB.
        await fetch('/api/stream/elicitation-complete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sessionId, elicitationId, oauthSessionUri: event.data.sessionId }),
        })
      } catch (error) {
        console.error('[useChat] Failed to signal elicitation complete:', error)
      }

      setSessionState(prev => ({ ...prev, pendingOAuth: null }))
    }

    const handleOAuthStorage = (event: StorageEvent) => {
      if (event.key !== 'oauth_completed' || !event.newValue) return
      try {
        const completed = JSON.parse(event.newValue)
        if (completed.elicitationId !== sessionState.pendingOAuth?.elicitationId) return
        setSessionState(prev => ({ ...prev, pendingOAuth: null }))
      } catch {
        // Ignore malformed cross-window completion signals.
      }
    }

    window.addEventListener('message', handleOAuthMessage)
    window.addEventListener('storage', handleOAuthStorage)
    return () => {
      window.removeEventListener('message', handleOAuthMessage)
      window.removeEventListener('storage', handleOAuthStorage)
    }
  }, [sessionState.pendingOAuth, sessionId])

  // ==================== ACTIONS ====================
  const newChat = useCallback(async () => {
    // Invalidate current session
    currentSessionIdRef.current = `temp_${Date.now()}`
    stopPolling()

    const success = await apiNewChat()
    if (success) {
      setSessionState({
        reasoning: null,
        streaming: null,
        toolExecutions: [],
        browserSession: null,
        browserProgress: undefined,
        researchProgress: undefined,
        interrupt: null,
        pendingOAuth: null
      })
      setUIState(prev => ({ ...prev, isTyping: false, agentStatus: 'idle' }))
      setMessages([])
    }
  }, [apiNewChat, stopPolling])

  const respondToInterrupt = useCallback(async (interruptId: string, response: string) => {
    if (!sessionState.interrupt) return

    setSessionState(prev => ({ ...prev, interrupt: null }))

    const isResearchInterrupt = sessionState.interrupt.interrupts.some(
      int => int.reason?.tool_name === 'research_agent'
    )

    const agentStatus: 'thinking' | 'researching' = isResearchInterrupt ? 'researching' : 'thinking'
    setUIState(prev => ({ ...prev, isTyping: true, agentStatus }))

    try {
      await apiSendMessage(
        JSON.stringify([{ interruptResponse: { interruptId, response } }]),
        undefined,
        undefined,
        () => setUIState(prev => ({ ...prev, isTyping: false, agentStatus: 'idle' })),
      )
    } catch (error) {
      console.error('[Interrupt] Failed to respond to interrupt:', error)
      setUIState(prev => ({ ...prev, isTyping: false, agentStatus: 'idle' }))
    }
  }, [sessionState.interrupt, apiSendMessage])

  const sendMessage = useCallback(async (text: string, files?: File[], systemPrompt?: string, selectedArtifactId?: string | null) => {
    if (!text.trim() && (!files || files.length === 0)) return

    const now = Date.now()
    const userMessage: Message = {
      id: String(now),
      text,
      sender: 'user',
      timestamp: new Date().toLocaleTimeString(),
      rawTimestamp: now,
      ...(files && files.length > 0 ? {
        uploadedFiles: files.map(file => ({
          name: file.name,
          type: file.type,
          size: file.size
        }))
      } : {})
    }

    currentTurnIdRef.current = `turn_${crypto.randomUUID()}`
    const requestStartTime = Date.now()

    setMessages(prev => [...prev, userMessage])
    setUIState(prev => ({
      ...prev,
      isTyping: true,
      agentStatus: 'thinking',
      latencyMetrics: {
        requestStartTime,
        timeToFirstToken: null,
        endToEndLatency: null
      }
    }))
    setSessionState(prev => ({
      ...prev,
      reasoning: null,
      streaming: null,
      toolExecutions: [],
      researchProgress: undefined
    }))
    currentToolExecutionsRef.current = []

    // Track uploaded file types for workspace refresh at RUN_FINISHED
    uploadedDocTypesRef.current.clear()
    if (files && files.length > 0) {
      for (const file of files) {
        const mime = file.type || ''
        if (mime.startsWith('image/')) {
          uploadedDocTypesRef.current.add('image')
        } else if (mime.includes('wordprocessingml') || mime === 'application/msword') {
          uploadedDocTypesRef.current.add('word')
        } else if (mime.includes('spreadsheetml') || mime === 'application/vnd.ms-excel') {
          uploadedDocTypesRef.current.add('excel')
        } else if (mime.includes('presentationml') || mime === 'application/vnd.ms-powerpoint') {
          uploadedDocTypesRef.current.add('powerpoint')
        }
      }
    }

    const messageToSend = text.trim() || (files && files.length > 0 ? "Please analyze the uploaded file(s)." : "")

    await apiSendMessage(
      messageToSend,
      files,
      () => {},
      () => {
        setSessionState(prev => ({
          reasoning: null,
          streaming: null,
          toolExecutions: [],
          browserSession: prev.browserSession,
          browserProgress: undefined,
          researchProgress: undefined,
          interrupt: null,
          pendingOAuth: null
        }))
        setUIState(prev => ({ ...prev, agentStatus: 'idle', isTyping: false }))
      },
      systemPrompt,
      selectedArtifactId
    )
  }, [apiSendMessage, setUIState])

  // localStorage key for compact recovery across browser refresh
  const getCompactPendingKey = (sid: string) => `compact_pending_${sid}`

  // Resume a pending compact (called on mount if localStorage has a pending compact)
  const resumeCompact = useCallback(async (sid: string, oldEventIds: string[]) => {
    console.log(`[compact] Resuming pending compact for session ${sid} (${oldEventIds.length} events to delete)`)
    setCompactingSessionId(sid)
    setUIState(prev => ({ ...prev, agentStatus: 'compacting', isTyping: true }))
    try {
      await apiCompactSession(oldEventIds)
      console.log('[compact] Resume: events deleted')
      localStorage.removeItem(getCompactPendingKey(sid))
      // Reload session from AgentCore Memory — now contains only the summary event
      await loadSessionWithPreferences(sid)
      console.log('[compact] Resume: session reloaded')
    } catch (error) {
      console.warn('[compact] Resume: error during compact resume:', error)
    } finally {
      setCompactingSessionId(null)
      setUIState(prev => ({ ...prev, agentStatus: 'idle', isTyping: false }))
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiCompactSession, setUIState, loadSessionWithPreferences])

  const compactSession = useCallback(async (): Promise<void> => {
    const currentSessionId = sessionId
    if (!currentSessionId) return

    setCompactingSessionId(currentSessionId)
    setUIState(prev => ({ ...prev, agentStatus: 'compacting', isTyping: true }))
    try {
      const summary = await apiSummarizeForCompact(messages)
      if (!summary) {
        setCompactingSessionId(null)
        setUIState(prev => ({ ...prev, agentStatus: 'idle', isTyping: false }))
        return
      }

      // Snapshot eventIds before sending summary so old events can be deleted safely
      const oldEventIds = await apiListSessionEvents()

      // Send summary as user message — this creates the event in memory AND gets an agent response.
      // Use apiSendMessage directly to detect backend rejection before proceeding with deletion.
      const summaryText = `Here is a summary of the previous session to continue our work:\n\n${summary}`
      const summaryMsgId = String(Date.now())
      setMessages(prev => [...prev, {
        id: summaryMsgId,
        text: summaryText,
        sender: 'user' as const,
        timestamp: new Date().toLocaleTimeString(),
        rawTimestamp: Date.now(),
      }])
      setUIState(prev => ({ ...prev, agentStatus: 'thinking', isTyping: true }))
      let summarySent = false
      await apiSendMessage(
        summaryText,
        undefined,
        () => { summarySent = true },
        () => { summarySent = false },
      )
      if (!summarySent) {
        setMessages(prev => prev.filter(m => m.id !== summaryMsgId))
        setCompactingSessionId(null)
        setUIState(prev => ({ ...prev, agentStatus: 'idle', isTyping: false }))
        return
      }

      setUIState(prev => ({ ...prev, agentStatus: 'compacting', isTyping: true }))
      localStorage.setItem(getCompactPendingKey(currentSessionId), JSON.stringify({ oldEventIds }))

      await apiCompactSession(oldEventIds)

      // Trim UI to summary + agent response. Do not reload from backend — the response is
      // already in the messages state from streaming, but backend write may not be committed yet.
      localStorage.removeItem(getCompactPendingKey(currentSessionId))
      setMessages(prev => {
        const summaryIdx = prev.findIndex(m => m.id === summaryMsgId)
        return summaryIdx >= 0 ? prev.slice(summaryIdx) : prev
      })
      setCompactingSessionId(null)
      setUIState(prev => ({ ...prev, agentStatus: 'idle', isTyping: false }))
    } catch (error) {
      console.error('[compact] Error during compact:', error)
      setCompactingSessionId(null)
      setUIState(prev => ({ ...prev, agentStatus: 'idle', isTyping: false }))
    }
  }, [sessionId, messages, apiSummarizeForCompact, apiListSessionEvents, apiCompactSession, setUIState, apiSendMessage, setMessages])

  // Truncate chat history from a specific user message (inclusive) onward
  const truncateFromMessage = useCallback(async (message: Message): Promise<void> => {
    const currentSessionId = sessionId
    if (!currentSessionId) return

    // History messages have their eventId as message.id (non-numeric string).
    // Newly sent messages in the current session have String(Date.now()) as id (numeric).
    const isHistoryMessage = isNaN(Number(message.id))
    const params = isHistoryMessage
      ? { fromEventId: message.id }
      : { fromTimestamp: message.rawTimestamp }

    if (!isHistoryMessage && !message.rawTimestamp) {
      console.warn('[truncate] Missing rawTimestamp for non-history message, aborting')
      return
    }

    console.log(`[truncate] Truncating from message ${message.id}`, params)

    // Optimistically remove the message and everything after it from the UI
    setMessages(prev => {
      const idx = prev.findIndex(m => m.id === message.id)
      return idx >= 0 ? prev.slice(0, idx) : prev
    })

    try {
      await apiTruncateSession(params)
      console.log('[truncate] Backend truncation complete')
    } catch (error) {
      console.error('[truncate] Error truncating session:', error)
      await loadSessionWithPreferences(currentSessionId)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, apiTruncateSession, setMessages, loadSessionWithPreferences])

  // On session load, check if there is a pending compact to resume (survives browser refresh)
  useEffect(() => {
    if (!sessionId) return
    const pending = localStorage.getItem(getCompactPendingKey(sessionId))
    if (!pending) return
    try {
      const { oldEventIds } = JSON.parse(pending)
      if (Array.isArray(oldEventIds)) {
        resumeCompact(sessionId, oldEventIds)
      }
    } catch {
      localStorage.removeItem(getCompactPendingKey(sessionId))
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  const stopGeneration = useCallback(async () => {
    let previousStatus: AgentStatus = 'thinking'
    setUIState(prev => {
      previousStatus = prev.agentStatus
      return { ...prev, agentStatus: 'stopping' }
    })

    const stopped = await sendStopSignal()
    if (stopped) {
      // The durable stop request has been accepted; the local stream can now close.
      resetStreamingState()
      return
    }

    setUIState(prev => ({
      ...prev,
      agentStatus: previousStatus === 'stopping' ? 'thinking' : previousStatus,
    }))
  }, [sendStopSignal, resetStreamingState])

  const cancelOAuth = useCallback(() => {
    localStorage.removeItem('oauth_pending')
    setSessionState(prev => ({ ...prev, pendingOAuth: null }))
    void stopGeneration()
  }, [stopGeneration])

  // ==================== DERIVED STATE ====================
  const groupedMessages = useMemo(() => {
    const grouped: Array<{
      type: 'user' | 'assistant_turn'
      messages: Message[]
      id: string
    }> = []

    let currentAssistantTurn: Message[] = []

    for (const message of messages) {
      if (message.sender === 'user') {
        if (currentAssistantTurn.length > 0) {
          grouped.push({
            type: 'assistant_turn',
            messages: currentAssistantTurn,
            id: `turn_${currentAssistantTurn[0].id}`
          })
          currentAssistantTurn = []
        }
        grouped.push({
          type: 'user',
          messages: [message],
          id: `user_${message.id}`
        })
      } else {
        currentAssistantTurn.push(message)
      }
    }

    if (currentAssistantTurn.length > 0) {
      grouped.push({
        type: 'assistant_turn',
        messages: currentAssistantTurn,
        id: `turn_${currentAssistantTurn[0].id}`
      })
    }

    return grouped
  }, [messages])

  // Update per-session model config (React state + global default via API)
  const updateModelConfig = useCallback((modelId: string, temperature?: number) => {
    setCurrentModelId(modelId)
    if (temperature !== undefined) {
      setCurrentTemperature(temperature)
    }
    // Also persist as global default for new chats
    apiPost('model/config/update', {
      model_id: modelId,
      ...(temperature !== undefined && { temperature }),
    }, {
      headers: sessionId ? { 'X-Session-ID': sessionId } : {},
    }).catch(error => {
      console.warn('[useChat] Failed to update global model config:', error)
    })
  }, [sessionId])

  const toggleProgressPanel = useCallback(() => {
    setUIState(prev => ({ ...prev, showProgressPanel: !prev.showProgressPanel }))
  }, [])

  // Add voice tool execution (mirrors text mode's handleToolUseEvent pattern)
  // Tool executions are added as separate isToolMessage messages
  const addVoiceToolExecution = useCallback((toolExecution: ToolExecution) => {
    console.log(`[useChat] addVoiceToolExecution: ${toolExecution.toolName}, id=${toolExecution.id}`)

    setMessages(prev => {
      // First, finalize any current assistant streaming message (like text mode does)
      // Find by properties instead of refs for React state consistency
      let updated = prev.map(msg => {
        if (msg.isVoiceMessage && msg.isStreaming && msg.sender === 'bot') {
          console.log(`[useChat] Finalizing assistant streaming message before tool: ${msg.id}`)
          return { ...msg, isStreaming: false }
        }
        return msg
      })

      // Check if there's an existing tool message we should update
      const existingToolMsgIdx = updated.findIndex(msg =>
        msg.isToolMessage &&
        msg.isVoiceMessage &&
        msg.toolExecutions?.some(te => te.id === toolExecution.id)
      )

      if (existingToolMsgIdx >= 0) {
        // Update existing tool execution
        return updated.map((msg, idx) => {
          if (idx === existingToolMsgIdx && msg.toolExecutions) {
            return {
              ...msg,
              toolExecutions: msg.toolExecutions.map(te =>
                te.id === toolExecution.id ? toolExecution : te
              ),
            }
          }
          return msg
        })
      }

      // Create new tool message (like text mode's isToolMessage pattern)
      return [...updated, {
        id: `voice_tool_${crypto.randomUUID()}`,
        text: '',
        sender: 'bot' as const,
        timestamp: new Date().toISOString(),
        isVoiceMessage: true,
        isToolMessage: true,
        toolExecutions: [toolExecution],
      }]
    })
  }, [])

  // Set voice status (called by useVoiceChat via callback)
  const setVoiceStatus = useCallback((status: AgentStatus) => {
    setUIState(prev => ({ ...prev, agentStatus: status }))
  }, [])

  // Add artifact message (called when a workflow creates an artifact)
  const addArtifactMessage = useCallback((artifact: { id: string; type: string; title: string; wordCount?: number }) => {
    const newMessage: Message = {
      id: `artifact_${Date.now()}`,
      text: '',  // No text, just the artifact reference
      sender: 'bot',
      timestamp: new Date().toISOString(),
      artifactReference: {
        id: artifact.id,
        type: artifact.type,
        title: artifact.title,
        wordCount: artifact.wordCount
      }
    }
    setMessages(prev => [...prev, newMessage])
  }, [])

  // Finalize current voice message (called when bidi_response_complete, tool_use, or interruption)
  // This marks ALL streaming voice messages as complete (both user and assistant)
  // This is safe because:
  // - bidi_response_complete: assistant finished speaking
  // - tool_use: assistant pausing for tool execution
  // - bidi_interruption: user interrupted, assistant should stop
  // In all cases, any pending streaming message should be finalized.
  const finalizeVoiceMessage = useCallback(() => {
    console.log('[useChat] finalizeVoiceMessage called')

    setMessages(prev => {
      // Find ALL streaming voice messages and finalize them
      const hasStreamingMessages = prev.some(msg =>
        msg.isVoiceMessage && msg.isStreaming === true
      )

      if (!hasStreamingMessages) {
        console.log('[useChat] No streaming voice messages to finalize')
        return prev
      }

      return prev.map(msg => {
        if (msg.isVoiceMessage && msg.isStreaming === true) {
          const finalId = `voice_${crypto.randomUUID()}`
          console.log(`[useChat] Finalizing ${msg.sender} message: ${msg.id} -> ${finalId}`)
          return { ...msg, id: finalId, isStreaming: false }
        }
        return msg
      })
    })
  }, [])

  // Update voice message with turn-based accumulation
  //
  // Key insight: Nova Sonic sends multiple FINAL transcripts for a single utterance.
  // We must NOT finalize on each is_final=true, but accumulate until:
  // 1. Role changes (user → assistant or vice versa)
  // 2. Explicit finalize via bidi_response_complete, tool_use, or interruption
  //
  // Message lifecycle:
  // 1. First delta for a role → Create new message with isStreaming=true
  // 2. Subsequent deltas (same role) → APPEND delta to same message (ignore is_final)
  // 3. Role changes → Finalize previous role's message, create new for new role
  // 4. Explicit finalize events → Call finalizeVoiceMessage() separately
  //
  // IMPORTANT: is_final from Nova Sonic marks end of a "segment", not end of "turn".
  // A turn can have multiple segments. Only finalize on role change or explicit events.
  const updateVoiceMessage = useCallback((role: 'user' | 'assistant', deltaText: string, _isFinal: boolean) => {
    const sender = role === 'user' ? 'user' : 'bot'
    const otherSender = role === 'user' ? 'bot' : 'user'

    console.log(`[useChat] updateVoiceMessage: role=${role}, delta="${deltaText.substring(0, 50)}..."`)

    setMessages(prev => {
      // Step 1: Check if there's a streaming message from the OTHER role
      // If so, we need to finalize it first (role change occurred)
      const otherStreamingIdx = prev.findIndex(msg =>
        msg.isVoiceMessage &&
        msg.isStreaming === true &&
        msg.sender === otherSender
      )

      let updatedMessages = prev

      if (otherStreamingIdx >= 0) {
        // Finalize the other role's streaming message (role change)
        const otherMsg = prev[otherStreamingIdx]
        const finalId = `voice_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
        console.log(`[useChat] Role changed: finalizing ${otherSender} message: ${otherMsg.id} -> ${finalId}`)

        updatedMessages = prev.map((msg, idx) => {
          if (idx === otherStreamingIdx) {
            return { ...msg, id: finalId, isStreaming: false }
          }
          return msg
        })
      }

      // Step 2: Find existing streaming message for THIS role
      const streamingMsgIdx = updatedMessages.findIndex(msg =>
        msg.isVoiceMessage &&
        msg.isStreaming === true &&
        msg.sender === sender
      )

      if (streamingMsgIdx >= 0) {
        // Append delta to existing streaming message (same role)
        const existingMsg = updatedMessages[streamingMsgIdx]
        const newText = (existingMsg.text || '') + deltaText

        console.log(`[useChat] Appending to streaming ${sender} message: id=${existingMsg.id}, newLen=${newText.length}`)

        return updatedMessages.map((msg, idx) => {
          if (idx === streamingMsgIdx) {
            return { ...msg, text: newText }
          }
          return msg
        })
      } else {
        // No streaming message for this role - create new one
        const newId = `voice_streaming_${role}_${Date.now()}`

        console.log(`[useChat] Creating NEW voice message for ${sender}: ${newId}, delta="${deltaText.substring(0, 30)}..."`)

        return [...updatedMessages, {
          id: newId,
          text: deltaText,
          sender,
          timestamp: new Date().toISOString(),
          isVoiceMessage: true,
          isStreaming: true,  // Always start as streaming, finalize explicitly
        }]
      }
    })
  }, [])

  // ==================== CLEANUP ====================
  useEffect(() => {
    return cleanup
  }, [cleanup])

  // ==================== RETURN ====================
  return {
    messages,
    groupedMessages,
    isConnected: uiState.isConnected,
    isTyping: uiState.isTyping,
    agentStatus: uiState.agentStatus,
    currentToolExecutions: sessionState.toolExecutions,
    currentReasoning: sessionState.reasoning,
    showProgressPanel: uiState.showProgressPanel,
    toggleProgressPanel,
    sendMessage,
    stopGeneration,
    newChat,
    compactSession,
    truncateFromMessage,
    sessionId,
    isLoadingMessages,
    isCompacting: compactingSessionId !== null && compactingSessionId === sessionId,
    loadSession: loadSessionWithPreferences,
    browserSession: sessionState.browserSession,
    browserProgress: sessionState.browserProgress,
    researchProgress: sessionState.researchProgress,
    codeProgress: sessionState.codeProgress,
    respondToInterrupt,
    currentInterrupt: sessionState.interrupt,
    // Per-session model state
    currentModelId,
    currentTemperature,
    updateModelConfig,
    swarmProgress: sessionState.swarmProgress,
    // Voice mode
    addVoiceToolExecution,
    updateVoiceMessage,
    setVoiceStatus,
    finalizeVoiceMessage,
    // Artifact message
    addArtifactMessage,
    // OAuth state
    pendingOAuth: sessionState.pendingOAuth,
    cancelOAuth,
    // SSE reconnection state
    isReconnecting,
    reconnectAttempt,
  }
}
