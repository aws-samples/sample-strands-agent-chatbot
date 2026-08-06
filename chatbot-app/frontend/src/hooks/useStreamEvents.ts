import { useCallback, useRef, startTransition, useEffect } from 'react'
import { flushSync } from 'react-dom'
import { EventType, type TextMessageStartEvent, type TextMessageContentEvent, type TextMessageEndEvent, type ToolCallStartEvent, type ToolCallArgsEvent, type ToolCallEndEvent, type ToolCallResultEvent, type RunFinishedEvent, type RunErrorEvent, type CustomEvent } from '@ag-ui/core'
import { Message, ToolExecution } from '@/types/chat'
import { AGUIStreamEvent, ChatSessionState, ChatUIState, WorkspaceFile, SWARM_AGENT_DISPLAY_NAMES, SwarmAgentStep, TokenUsage } from '@/types/events'
import { useMetadataTracking } from './useMetadataTracking'
import { useTextBuffer } from './useTextBuffer'
import { A2A_TOOLS_REQUIRING_POLLING, isA2ATool, getAgentStatusForTool } from './usePolling'
import { fetchAuthSession } from 'aws-amplify/auth'
import { updateLastActivity } from '@/config/session'
import { TOOL_TO_DOC_TYPE, DOC_TYPE_TO_TOOL_TYPE, TOOL_TYPE_TO_DOC_TYPE, DocumentType } from '@/config/document-tools'
import { ExtractedDataInfo } from './useCanvasHandlers'

// Word document info from workspace API
export interface WorkspaceDocument {
  filename: string
  size_kb: string
  last_modified: string
  s3_key: string
  tool_type: string
}

interface UseStreamEventsProps {
  sessionState: ChatSessionState
  setSessionState: React.Dispatch<React.SetStateAction<ChatSessionState>>
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
  setUIState: React.Dispatch<React.SetStateAction<ChatUIState>>
  uiState: ChatUIState
  currentToolExecutionsRef: React.MutableRefObject<ToolExecution[]>
  currentTurnIdRef: React.MutableRefObject<string | null>
  startPollingRef: React.MutableRefObject<((sessionId: string) => void) | null>
  stopPollingRef: React.MutableRefObject<(() => void) | null>
  sessionId: string | null
  onArtifactUpdated?: () => void  // Callback when artifact is updated via update_artifact tool
  onWordDocumentsCreated?: (documents: WorkspaceDocument[]) => void  // Callback when Word documents are created
  onExcelDocumentsCreated?: (documents: WorkspaceDocument[]) => void  // Callback when Excel documents are created
  onPptDocumentsCreated?: (documents: WorkspaceDocument[]) => void  // Callback when PowerPoint documents are created
  onDiagramCreated?: (s3Key: string, filename: string) => void  // Callback when diagram is generated
  onBrowserSessionDetected?: (browserSessionId: string, browserId: string) => void  // Callback when browser session is first detected
  onExtractedDataCreated?: (data: ExtractedDataInfo) => void  // Callback when browser_extract creates artifact
  onExcalidrawCreated?: (data: { elements: any[]; appState: any; title: string }, toolCallId: string) => void  // Callback when excalidraw diagram is created
  uploadedDocTypesRef?: React.MutableRefObject<Set<DocumentType>>  // Doc types from user file uploads (set before send)
}

export const useStreamEvents = ({
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
  onArtifactUpdated,
  onWordDocumentsCreated,
  onExcelDocumentsCreated,
  onPptDocumentsCreated,
  onDiagramCreated,
  onBrowserSessionDetected,
  onExtractedDataCreated,
  onExcalidrawCreated,
  uploadedDocTypesRef
}: UseStreamEventsProps) => {
  // Refs to track streaming state synchronously (avoid React batching issues)
  const streamingStartedRef = useRef(false)
  const streamingIdRef = useRef<string | null>(null)
  const completeProcessedRef = useRef(false)
  const tokenUsageRef = useRef<TokenUsage | null>(null)
  const pendingAssistantTurnBoundaryRef = useRef(false)

  // Accumulates TOOL_CALL_ARGS deltas keyed by toolCallId
  const toolInputAccumulatorRef = useRef<Record<string, string>>({})

  // Event deduplication for SSE reconnection (tracks processed "eventId:type" keys)
  const processedEventIdsRef = useRef(new Set<string>())

  // Swarm mode state
  const swarmModeRef = useRef<{
    isActive: boolean
    nodeHistory: string[]
    agentSteps: SwarmAgentStep[]
  }>({ isActive: false, nodeHistory: [], agentSteps: [] })

  // Latency tracking hook (encapsulates all latency-related refs and logic)
  const metadataTracking = useMetadataTracking()

  // Text buffer for smooth streaming (reduces re-renders by batching updates)
  // Note: onFlush callback is passed to startFlushing() when streaming starts,
  // not at initialization, to avoid stale closure issues with streamingIdRef
  const textBuffer = useTextBuffer({ flushInterval: 50 })

  const consumeAssistantTurnBoundary = useCallback(() => {
    if (!pendingAssistantTurnBoundaryRef.current) return {}
    pendingAssistantTurnBoundaryRef.current = false
    return { startsNewAssistantTurn: true as const }
  }, [])

  const handleReasoningEvent = useCallback((data: CustomEvent) => {
    const ev = (data as any).value
    // Swarm mode: capture reasoning for "Show agents"
    if (swarmModeRef.current.isActive) {
      if (swarmModeRef.current.agentSteps.length > 0) {
        const stepIndex = swarmModeRef.current.agentSteps.length - 1
        const currentStep = swarmModeRef.current.agentSteps[stepIndex]
        const updatedStep = {
          ...currentStep,
          reasoningText: (currentStep.reasoningText || '') + ev.text
        }
        swarmModeRef.current.agentSteps[stepIndex] = updatedStep
        setSessionState(prev => ({
          ...prev,
          swarmProgress: prev.swarmProgress ? {
            ...prev.swarmProgress,
            agentSteps: [...swarmModeRef.current.agentSteps]
          } : prev.swarmProgress
        }))
      }
      return
    }
    // Normal mode
    setSessionState(prev => ({
      ...prev,
      reasoning: { text: ev.text, isActive: true }
    }))
  }, [setSessionState])

  const handleTextMessageStartEvent = useCallback((event: TextMessageStartEvent) => {
    // Swarm mode: non-responder doesn't create chat messages
    if (swarmModeRef.current.isActive) {
      const currentNode = swarmModeRef.current.nodeHistory[swarmModeRef.current.nodeHistory.length - 1]
      if (currentNode !== 'responder') {
        return
      }
      // Responder: fall through to normal streaming logic
    }

    // Finalize reasoning step if active
    if (sessionState.reasoning?.isActive) {
      setSessionState(prev => ({
        ...prev,
        reasoning: prev.reasoning ? { ...prev.reasoning, isActive: false } : null
      }))
    }

    // Create new streaming message
    streamingStartedRef.current = true
    streamingIdRef.current = event.messageId
    const turnBoundary = consumeAssistantTurnBoundary()

    // Create message with empty text - buffer will populate it
    setMessages(prevMsgs => [...prevMsgs, {
      id: event.messageId,
      text: '', // Start empty, buffer will fill
      sender: 'bot',
      timestamp: new Date().toISOString(),
      isStreaming: true,
      images: [],
      ...turnBoundary,
    }])

    setSessionState(prev => ({
      ...prev,
      streaming: { text: '', id: Date.now() }  // Start empty
    }))

    // Start buffering with flush callback that captures current streamingIdRef
    textBuffer.startFlushing((bufferedText) => {
      const streamingId = streamingIdRef.current
      if (!streamingId) return

      // Update message with buffered text
      setMessages(prevMsgs => prevMsgs.map(msg =>
        msg.id === streamingId
          ? { ...msg, text: bufferedText }
          : msg
      ))

      // Update session state
      setSessionState(prev => ({
        ...prev,
        streaming: prev.streaming ? { ...prev.streaming, text: bufferedText } : null
      }))
    })

    // Always record TTFT on first text — regardless of what tools ran before
    const ttft = metadataTracking.recordTTFT()

    setUIState(prevUI => {
      // Don't change status if stopping or in A2A agent mode
      if (prevUI.agentStatus === 'stopping' ||
          prevUI.agentStatus === 'researching') {
        return prevUI
      }
      return {
        ...prevUI,
        agentStatus: 'responding',
        latencyMetrics: { ...prevUI.latencyMetrics, timeToFirstToken: ttft ?? prevUI.latencyMetrics.timeToFirstToken ?? null }
      }
    })
  }, [sessionState, setSessionState, setMessages, setUIState, streamingStartedRef, streamingIdRef, metadataTracking, textBuffer, consumeAssistantTurnBoundary])

  const handleTextMessageContentEvent = useCallback((event: TextMessageContentEvent) => {
    // Swarm mode: non-responder captures in agentSteps
    if (swarmModeRef.current.isActive) {
      const currentNode = swarmModeRef.current.nodeHistory[swarmModeRef.current.nodeHistory.length - 1]

      if (currentNode !== 'responder') {
        if (swarmModeRef.current.agentSteps.length > 0) {
          const stepIndex = swarmModeRef.current.agentSteps.length - 1
          const currentStep = swarmModeRef.current.agentSteps[stepIndex]
          const updatedStep = {
            ...currentStep,
            responseText: (currentStep.responseText || '') + event.delta
          }
          swarmModeRef.current.agentSteps[stepIndex] = updatedStep
          setSessionState(prev => ({
            ...prev,
            swarmProgress: prev.swarmProgress ? {
              ...prev.swarmProgress,
              agentSteps: [...swarmModeRef.current.agentSteps]
            } : prev.swarmProgress
          }))
        }
        return // Non-responder: only update SwarmProgress, no chat message
      }
      // Responder: fall through to normal streaming logic
    }

    // Append chunk to buffer (not directly to state)
    textBuffer.appendChunk(event.delta)
  }, [setSessionState, setMessages, setUIState, textBuffer])

  const handleTextMessageEndEvent = useCallback((_event: TextMessageEndEvent) => {
    // Swarm mode: non-responder has no streaming message to close
    if (swarmModeRef.current.isActive) {
      const currentNode = swarmModeRef.current.nodeHistory[swarmModeRef.current.nodeHistory.length - 1]
      if (currentNode !== 'responder') {
        return
      }
    }

    if (!streamingStartedRef.current || !streamingIdRef.current) return

    // Flush remaining buffer and capture final text
    const finalText = textBuffer.reset()

    const ttft = uiState.latencyMetrics.timeToFirstToken
    setMessages(prevMsgs => prevMsgs.map(msg => {
      if (msg.id === streamingIdRef.current) {
        return {
          ...msg,
          // Apply final buffered text in the same update to avoid React batching race
          ...(finalText && { text: finalText }),
          isStreaming: false,
          ...(ttft && !msg.latencyMetrics && { latencyMetrics: { timeToFirstToken: ttft } })
        }
      }
      return msg
    }))

    // Clear the "currently streaming" flag but keep streamingIdRef for RUN_FINISHED
    // to apply latency metrics and workspace documents to the correct message
    streamingStartedRef.current = false
  }, [uiState, setMessages, streamingStartedRef, streamingIdRef, textBuffer])

  const handleToolCallStartEvent = useCallback((event: ToolCallStartEvent) => {
    // Track tool in swarm mode for expanded view
    if (swarmModeRef.current.isActive && swarmModeRef.current.agentSteps.length > 0) {
      const stepIndex = swarmModeRef.current.agentSteps.length - 1
      const currentStep = swarmModeRef.current.agentSteps[stepIndex]
      // Create new step object with updated toolCalls
      const updatedStep = {
        ...currentStep,
        toolCalls: [...(currentStep.toolCalls || []), { toolName: event.toolCallName, status: 'running' as const }]
      }
      swarmModeRef.current.agentSteps[stepIndex] = updatedStep

      setSessionState(prev => ({
        ...prev,
        swarmProgress: prev.swarmProgress ? {
          ...prev.swarmProgress,
          currentAction: `Using ${event.toolCallName}...`,
          agentSteps: [...swarmModeRef.current.agentSteps]
        } : prev.swarmProgress
      }))

      // For responder's tools (like create_visualization), continue processing
      // to create tool message for rendering. Other agents return early.
      const currentNode = swarmModeRef.current.nodeHistory[swarmModeRef.current.nodeHistory.length - 1]
      if (currentNode !== 'responder') {
        return
      }
    }

    // Tool execution started - update agent status using shared utility
    const agentStatus = getAgentStatusForTool(event.toolCallName)

    setUIState(prev => ({
      ...prev,
      isTyping: true,
      agentStatus
    }))

    // Start polling for tool execution progress updates
    const needsPolling = isA2ATool(event.toolCallName)
    if (needsPolling && sessionId && startPollingRef.current) {
      startPollingRef.current(sessionId)
    }

    // Flush buffer and capture final text before tool execution
    const finalText = textBuffer.reset()

    // Finalize current streaming message before adding tool
    if (streamingStartedRef.current && streamingIdRef.current) {
      const ttft = uiState.latencyMetrics.timeToFirstToken
      setMessages(prevMsgs => prevMsgs.map(msg => {
        if (msg.id === streamingIdRef.current) {
          return {
            ...msg,
            // Apply final buffered text in the same update to avoid React batching race
            ...(finalText && { text: finalText }),
            isStreaming: false,
            ...(ttft && !msg.latencyMetrics && { latencyMetrics: { timeToFirstToken: ttft } })
          }
        }
        return msg
      }))

      // Reset refs so next response creates a new message (maintains correct order)
      streamingStartedRef.current = false
      streamingIdRef.current = null
    }

    // At TOOL_CALL_START, input is not yet available (comes via TOOL_CALL_ARGS/END)
    const normalizedInput = {}

    // Check if tool execution already exists
    const existingToolIndex = currentToolExecutionsRef.current.findIndex(tool => tool.id === event.toolCallId)

    if (existingToolIndex >= 0) {
      // Update existing tool execution (safety branch — TOOL_CALL_START should always be new)
      const updatedExecutions = [...currentToolExecutionsRef.current]
      updatedExecutions[existingToolIndex] = {
        ...updatedExecutions[existingToolIndex],
        toolInput: normalizedInput
      }

      currentToolExecutionsRef.current = updatedExecutions

      setSessionState(prev => ({
        ...prev,
        toolExecutions: updatedExecutions
      }))

      setMessages(prevMessages => prevMessages.map(msg => {
        if (msg.isToolMessage && msg.toolExecutions) {
          const updatedToolExecutions = msg.toolExecutions.map(tool =>
            tool.id === event.toolCallId
              ? { ...tool, toolInput: normalizedInput }
              : tool
          )
          return { ...msg, toolExecutions: updatedToolExecutions }
        }
        return msg
      }))
    } else {
      // Create new tool execution
      const newToolExecution: ToolExecution = {
        id: event.toolCallId,
        toolName: event.toolCallName,
        toolInput: normalizedInput,
        reasoning: [],
        isComplete: false,
        isExpanded: true
      }

      const updatedExecutions = [...currentToolExecutionsRef.current, newToolExecution]
      currentToolExecutionsRef.current = updatedExecutions

      // Update session state
      setSessionState(prev => ({
        ...prev,
        toolExecutions: updatedExecutions
      }))

      // Create new tool message immediately (not in startTransition)
      // Tool container should appear right away with "Loading parameters..." state
      const toolMessageId = String(Date.now())
      const turnBoundary = consumeAssistantTurnBoundary()
      setMessages(prevMessages => [...prevMessages, {
        id: toolMessageId,
        text: '',
        sender: 'bot',
        timestamp: new Date().toISOString(),
        toolExecutions: [newToolExecution],
        isToolMessage: true,
        turnId: currentTurnIdRef.current || undefined,
        ...turnBoundary,
      }])
    }
  }, [currentToolExecutionsRef, currentTurnIdRef, setSessionState, setMessages, setUIState, uiState, textBuffer, consumeAssistantTurnBoundary])

  const handleToolCallArgsEvent = useCallback((event: ToolCallArgsEvent) => {
    const current = toolInputAccumulatorRef.current[event.toolCallId] || ''
    toolInputAccumulatorRef.current[event.toolCallId] = current + event.delta
  }, [toolInputAccumulatorRef])

  const handleToolCallEndEvent = useCallback((event: ToolCallEndEvent) => {
    const accumulated = toolInputAccumulatorRef.current[event.toolCallId] || ''
    delete toolInputAccumulatorRef.current[event.toolCallId]

    let parsedInput: any = {}
    try {
      parsedInput = accumulated ? JSON.parse(accumulated) : {}
    } catch {
      parsedInput = {}
    }

    const normalizedInput = parsedInput === null || parsedInput === undefined ? {} : parsedInput

    const updatedExecutions = currentToolExecutionsRef.current.map(tool =>
      tool.id === event.toolCallId ? { ...tool, toolInput: normalizedInput } : tool
    )
    currentToolExecutionsRef.current = updatedExecutions

    setSessionState(prev => ({
      ...prev,
      toolExecutions: updatedExecutions
    }))

    setMessages(prevMessages => prevMessages.map(msg => {
      if (msg.isToolMessage && msg.toolExecutions) {
        const updatedToolExecutions = msg.toolExecutions.map(tool =>
          tool.id === event.toolCallId ? { ...tool, toolInput: normalizedInput } : tool
        )
        return { ...msg, toolExecutions: updatedToolExecutions }
      }
      return msg
    }))
  }, [toolInputAccumulatorRef, currentToolExecutionsRef, setSessionState, setMessages])

  const handleToolCallResultEvent = useCallback((event: ToolCallResultEvent) => {
    // AG-UI: content is a JSON string: '{"result":"...","metadata":{...},"images":[...],"status":"..."}'
    let parsedContent: any = {}
    try {
      if (event.content) {
        parsedContent = JSON.parse(event.content)
      }
    } catch {
      parsedContent = { result: event.content }
    }

    const toolOutput   = parsedContent.result   ?? ''
    const toolMetadata = parsedContent.metadata
    const toolImages   = parsedContent.images    ?? []
    const toolStatus   = parsedContent.status

    // Find the tool name from current executions
    const toolExecution = currentToolExecutionsRef.current.find(tool => tool.id === event.toolCallId)
    const toolName = toolExecution?.toolName
    const isCancelled = toolStatus === 'error'

    // Track tool completion in swarm mode for expanded view
    if (swarmModeRef.current.isActive && swarmModeRef.current.agentSteps.length > 0) {
      const stepIndex = swarmModeRef.current.agentSteps.length - 1
      const currentStep = swarmModeRef.current.agentSteps[stepIndex]
      if (currentStep.toolCalls) {
        // Create new toolCalls array with updated status
        const updatedToolCalls = currentStep.toolCalls.map(t =>
          t.status === 'running' ? { ...t, status: isCancelled ? 'failed' as const : 'completed' as const } : t
        )
        const updatedStep = { ...currentStep, toolCalls: updatedToolCalls }
        swarmModeRef.current.agentSteps[stepIndex] = updatedStep
      }

      const currentNode = swarmModeRef.current.nodeHistory[swarmModeRef.current.nodeHistory.length - 1]
      const displayName = SWARM_AGENT_DISPLAY_NAMES[currentNode] || currentNode

      // For responder's tools, keep SwarmProgress but it will auto-collapse
      // The tool message will render the chart directly in chat
      if (currentNode === 'responder') {
        setSessionState(prev => ({
          ...prev,
          swarmProgress: prev.swarmProgress ? {
            ...prev.swarmProgress,
            currentAction: `${displayName} working...`,
            agentSteps: [...swarmModeRef.current.agentSteps]
          } : prev.swarmProgress
        }))
      } else {
        setSessionState(prev => ({
          ...prev,
          swarmProgress: prev.swarmProgress ? {
            ...prev.swarmProgress,
            currentAction: `${displayName} working...`,
            agentSteps: [...swarmModeRef.current.agentSteps]
          } : prev.swarmProgress
        }))
        return  // Other agents return early
      }
    }

    // If A2A tool completed, transition from researching to thinking
    // This allows subsequent response events to properly transition to 'responding'
    if (toolName && isA2ATool(toolName)) {
      setUIState(prev => {
        if (prev.agentStatus === 'researching') {
          return { ...prev, agentStatus: 'thinking' }
        }
        return prev
      })
    }

    // If update_artifact tool completed successfully, notify parent to refresh artifacts
    if (toolName === 'update_artifact' && !isCancelled && onArtifactUpdated) {
      console.log('[useStreamEvents] update_artifact completed, triggering artifact refresh')
      onArtifactUpdated()
    }

    // If browser_extract tool completed successfully with artifact, open Canvas
    if (toolName === 'browser_extract' && !isCancelled && toolMetadata?.artifactId && onExtractedDataCreated) {
      console.log('[useStreamEvents] browser_extract completed, creating artifact:', toolMetadata.artifactId)
      // Parse extracted data from tool result
      const extractedDataMatch = toolOutput?.match(/\*\*Extracted Data\*\*:\s*```json\n([\s\S]*?)```/)
      const extractedContent = extractedDataMatch ? extractedDataMatch[1].trim() : '{}'
      const descriptionMatch = toolOutput?.match(/\*\*Description\*\*:\s*(.+)/)
      const title = descriptionMatch ? descriptionMatch[1].substring(0, 50) : 'Extracted Data'

      onExtractedDataCreated({
        artifactId: toolMetadata.artifactId,
        title,
        content: extractedContent,
        sourceUrl: toolMetadata.source_url || '',
        sourceTitle: toolMetadata.source_title || ''
      })
    }

    // Update tool execution with result
    // Filter out images if hideImageInChat metadata is set
    const shouldHideImages = toolMetadata?.hideImageInChat === true
    const filteredImages = shouldHideImages ? [] : toolImages

    const updatedExecutions = currentToolExecutionsRef.current.map(tool =>
      tool.id === event.toolCallId
        ? { ...tool, toolResult: toolOutput, metadata: toolMetadata, images: filteredImages, isComplete: true, isCancelled }
        : tool
    )

    currentToolExecutionsRef.current = updatedExecutions

    // Extract browser session info from metadata (for Live View)
    // Only set on first browser tool use to prevent unnecessary DCV reconnections
    const browserSessionUpdate: any = {}
    if (!sessionState.browserSession && toolMetadata?.browserSessionId) {
      const browserSession = {
        sessionId: toolMetadata.browserSessionId,
        browserId: toolMetadata.browserId || null
      }

      browserSessionUpdate.browserSession = browserSession

      // Notify parent about browser session detection (for Canvas integration)
      if (onBrowserSessionDetected) {
        onBrowserSessionDetected(browserSession.sessionId, browserSession.browserId || '')
      }

    }

    // Update state - A2A tools use high-priority updates so the artifact
    // creation chain (messages → researchData → useEffect) fires immediately.
    // Regular tools use startTransition to avoid blocking the UI.
    const isA2AResult = toolName && isA2ATool(toolName)

    const applyUpdates = () => {
      setSessionState(prev => ({
        ...prev,
        toolExecutions: updatedExecutions,
        ...browserSessionUpdate
      }))

      setMessages(prev => prev.map(msg => {
        if (msg.isToolMessage && msg.toolExecutions) {
          const updatedToolExecutions = msg.toolExecutions.map(tool =>
            tool.id === event.toolCallId
              ? { ...tool, toolResult: toolOutput, metadata: toolMetadata, images: filteredImages, isComplete: true }
              : tool
          )
          return {
            ...msg,
            toolExecutions: updatedToolExecutions
          }
        }
        return msg
      }))
    }

    if (isA2AResult) {
      applyUpdates()
    } else {
      startTransition(applyUpdates)
    }
  }, [currentToolExecutionsRef, sessionState, setSessionState, setMessages, setUIState])

  const handleCompleteEvent = useCallback(async (event: RunFinishedEvent) => {
    if (completeProcessedRef.current) return
    completeProcessedRef.current = true

    // Stop polling on stream completion - A2A tools are done
    if (stopPollingRef.current) {
      stopPollingRef.current()
    }

    // Flush any remaining buffered text before completing
    textBuffer.reset()

    const messageId = streamingIdRef.current

    // Normal complete flow
    if (messageId) {
      updateLastActivity()

      const currentSessionId = sessionStorage.getItem('chat-session-id')

      // Detect used document tools and fetch workspace files from S3
      let workspaceDocuments: Array<{ filename: string; tool_type: string }> = []

      const resolveDocType = (toolExec: { toolName: string; toolInput?: any; metadata?: any }): DocumentType | undefined => {
        const docType = TOOL_TO_DOC_TYPE[toolExec.toolName]
        if (docType) return docType
        // Fallback: metadata.tool_type (e.g. "powerpoint_presentation")
        if (toolExec.metadata?.tool_type) {
          return TOOL_TYPE_TO_DOC_TYPE[toolExec.metadata.tool_type]
        }
        return undefined
      }

      // Check tool executions for document tools
      const usedDocTypes = new Set<DocumentType>()
      for (const toolExec of currentToolExecutionsRef.current) {
        const docType = resolveDocType(toolExec)
        if (docType) {
          usedDocTypes.add(docType)
        }
      }

      // Include doc types from user file uploads (images, docs, etc.)
      if (uploadedDocTypesRef?.current) {
        for (const dt of uploadedDocTypesRef.current) {
          usedDocTypes.add(dt)
        }
        uploadedDocTypesRef.current.clear()
      }

      // Fetch workspace files for each used document type
      if (usedDocTypes.size > 0 && currentSessionId) {
        try {
          // Get auth headers for workspace API calls
          const workspaceHeaders: Record<string, string> = {
            'X-Session-ID': currentSessionId
          }
          try {
            const session = await fetchAuthSession()
            const token = session.tokens?.accessToken?.toString()
            if (token) {
              workspaceHeaders['Authorization'] = `Bearer ${token}`
            }
          } catch {
            // No auth session available - continue without auth header
          }

          // Extract output filenames from tool result metadata
          const wordOutputFilenames = new Set<string>()
          const excelOutputFilenames = new Set<string>()
          const pptOutputFilenames = new Set<string>()

          for (const toolExec of currentToolExecutionsRef.current) {
            const filename = toolExec.metadata?.filename
            if (!filename || !toolExec.isComplete || toolExec.isCancelled) continue

            const docType = resolveDocType(toolExec)
            if (docType === 'word') wordOutputFilenames.add(filename)
            else if (docType === 'excel') excelOutputFilenames.add(filename)
            else if (docType === 'powerpoint') pptOutputFilenames.add(filename)
          }

          let wordDocumentsForArtifact: WorkspaceDocument[] = []
          let excelDocumentsForArtifact: WorkspaceDocument[] = []
          let pptDocumentsForArtifact: WorkspaceDocument[] = []

          const fetchPromises = Array.from(usedDocTypes).map(async (docType) => {
            const response = await fetch(`/api/workspace/files?docType=${docType}`, {
              headers: workspaceHeaders
            })
            if (response.ok) {
              const data = await response.json()
              if (data.files && Array.isArray(data.files)) {
                const files = data.files.map((file: any) => ({
                  filename: file.filename,
                  size_kb: file.size_kb,
                  last_modified: file.last_modified,
                  s3_key: file.s3_key,
                  tool_type: DOC_TYPE_TO_TOOL_TYPE[docType] || file.tool_type
                }))

                // Collect only newly created/modified Word documents for artifact creation
                if (docType === 'word' && wordOutputFilenames.size > 0) {
                  wordDocumentsForArtifact = files.filter((f: WorkspaceDocument) =>
                    wordOutputFilenames.has(f.filename)
                  )
                }

                // Collect only newly created/modified Excel documents for artifact creation
                if (docType === 'excel' && excelOutputFilenames.size > 0) {
                  excelDocumentsForArtifact = files.filter((f: WorkspaceDocument) =>
                    excelOutputFilenames.has(f.filename)
                  )
                }

                // Collect only newly created/modified PowerPoint documents for artifact creation
                if (docType === 'powerpoint' && pptOutputFilenames.size > 0) {
                  pptDocumentsForArtifact = files.filter((f: WorkspaceDocument) =>
                    pptOutputFilenames.has(f.filename)
                  )
                }

                return files
              }
            }
            return []
          })

          const results = await Promise.all(fetchPromises)
          workspaceDocuments = results.flat()

          // Trigger Word document artifact creation callback (only for output files)
          if (wordDocumentsForArtifact.length > 0 && onWordDocumentsCreated) {
            onWordDocumentsCreated(wordDocumentsForArtifact)
          }

          // Trigger Excel document artifact creation callback (only for output files)
          if (excelDocumentsForArtifact.length > 0 && onExcelDocumentsCreated) {
            onExcelDocumentsCreated(excelDocumentsForArtifact)
          }

          // Trigger PowerPoint document artifact creation callback (only for output files)
          if (pptDocumentsForArtifact.length > 0 && onPptDocumentsCreated) {
            onPptDocumentsCreated(pptDocumentsForArtifact)
          }
        } catch (error) {
          console.error('[RUN_FINISHED] Workspace API failed:', error)
        }
      }

      // Trigger Excalidraw diagram artifact creation (JSON content direct from tool result)
      if (onExcalidrawCreated) {
        for (const toolExec of currentToolExecutionsRef.current) {
          if (!toolExec.isComplete || toolExec.isCancelled || !toolExec.toolResult) continue
          if (toolExec.toolName === 'create_excalidraw_diagram') {
            try {
              let result = JSON.parse(toolExec.toolResult)
              // skill_executor packages the inner tool's result as a JSON
              // string inside `result.result`; unwrap that extra layer.
              if (result?.result && typeof result.result === 'string') {
                try { result = JSON.parse(result.result) } catch { /* keep outer */ }
              }
              if (result.success && result.excalidraw_data) {
                onExcalidrawCreated(result.excalidraw_data, toolExec.id)
              }
            } catch {
              // Invalid JSON, skip
            }
          }
        }
      }

      // Trigger diagram artifact creation (uses s3_key from metadata directly, no workspace API needed)
      if (onDiagramCreated) {
        for (const toolExec of currentToolExecutionsRef.current) {
          if (!toolExec.isComplete || toolExec.isCancelled) continue
          const docType = resolveDocType(toolExec)
          if (docType === 'diagram' && toolExec.metadata?.s3_key && toolExec.metadata?.filename) {
            onDiagramCreated(toolExec.metadata.s3_key, toolExec.metadata.filename)
          }
        }
      }

      // Use workspace documents if fetched (usage/images/documents no longer on RUN_FINISHED)
      const finalDocuments = workspaceDocuments.length > 0
        ? workspaceDocuments
        : []

      const tokenUsage = tokenUsageRef.current ?? undefined

      const metrics = currentSessionId
        ? metadataTracking.recordE2E({
            sessionId: currentSessionId,
            messageId,
            tokenUsage,
            documents: finalDocuments
          })
        : metadataTracking.getMetrics()

      const ttftValue = 'ttft' in metrics ? metrics.ttft : metrics.timeToFirstToken
      const e2eValue = 'e2e' in metrics ? metrics.e2e : metrics.endToEndLatency

      setUIState(prev => ({
        ...prev,
        isTyping: false,
        showProgressPanel: false,
        agentStatus: 'idle',
        latencyMetrics: {
          ...prev.latencyMetrics,
          endToEndLatency: e2eValue ?? null
        }
      }))

      setMessages(prevMsgs => {
        let lastAssistantIndex = -1
        for (let i = prevMsgs.length - 1; i >= 0; i--) {
          if (prevMsgs[i].sender === 'bot') {
            lastAssistantIndex = i
            break
          }
        }

        return prevMsgs.map((msg, index) =>
          msg.id === messageId || (index === lastAssistantIndex && !messageId)
            ? {
                ...msg,
                isStreaming: false,
                images: msg.images || [],
                documents: finalDocuments,
                tokenUsage: tokenUsage ?? msg.tokenUsage,
                latencyMetrics: { timeToFirstToken: ttftValue, endToEndLatency: e2eValue }
              }
            : msg
        )
      })
    } else {
      setUIState(prev => {
        const requestStartTime = prev.latencyMetrics.requestStartTime
        const e2eLatency = requestStartTime ? Date.now() - requestStartTime : null
        return {
          ...prev,
          isTyping: false,
          showProgressPanel: false,
          agentStatus: 'idle',
          latencyMetrics: { ...prev.latencyMetrics, endToEndLatency: e2eLatency }
        }
      })
    }

    setSessionState(prev => ({
      reasoning: null,
      streaming: null,
      toolExecutions: [],
      browserSession: prev.browserSession,
      browserProgress: undefined,
      researchProgress: undefined,
      codeProgress: undefined,
      interrupt: null,
      swarmProgress: prev.swarmProgress,  // Preserve swarm progress for expanded view
      pendingOAuth: prev.pendingOAuth  // Preserve pending OAuth until completion callback
    }))

    streamingStartedRef.current = false
    streamingIdRef.current = null
    completeProcessedRef.current = false
    tokenUsageRef.current = null
    pendingAssistantTurnBoundaryRef.current = false
    metadataTracking.reset()
  }, [setSessionState, setMessages, setUIState, streamingStartedRef, streamingIdRef, completeProcessedRef, metadataTracking, currentToolExecutionsRef, textBuffer, stopPollingRef])

  const handleInitEvent = useCallback(() => {
    // Clear dedup set at the start of each new run so that events from the new
    // execution (which restart eventId from 1) are not mistakenly dropped.
    processedEventIdsRef.current.clear()
    pendingAssistantTurnBoundaryRef.current = true

    setUIState(prev => {
      if (prev.latencyMetrics.requestStartTime) {
        metadataTracking.startTracking(prev.latencyMetrics.requestStartTime)
      }
      if (prev.agentStatus !== 'idle') {
        return prev
      }

      // Only transition to 'thinking' if starting a new turn (idle -> thinking)
      return { ...prev, isTyping: true, agentStatus: 'thinking' }
    })
  }, [setUIState, metadataTracking])

  const handleErrorEvent = useCallback((event: RunErrorEvent) => {
    // Stop polling on error
    if (stopPollingRef.current) {
      stopPollingRef.current()
    }

    // Reset buffer on error
    textBuffer.reset()
    const turnBoundary = consumeAssistantTurnBoundary()

    setMessages(prev => [...prev, {
      id: String(Date.now()),
      text: event.message,
      sender: 'bot',
      timestamp: new Date().toISOString(),
      ...turnBoundary,
    }])

    setUIState(prev => {
      const requestStartTime = prev.latencyMetrics.requestStartTime
      const e2eLatency = requestStartTime ? Date.now() - requestStartTime : null

      return {
        ...prev,
        isTyping: false,
        agentStatus: 'idle',
        latencyMetrics: {
          ...prev.latencyMetrics,
          endToEndLatency: e2eLatency
        }
      }
    })
    // Preserve browserSession even on error - Live View should remain available
    setSessionState(prev => ({
      reasoning: null,
      streaming: null,
      toolExecutions: [],
      browserSession: prev.browserSession,  // Preserve browser session on error
      browserProgress: undefined,  // Clear browser progress on error
      researchProgress: undefined,  // Clear research progress on error
      codeProgress: undefined,
      interrupt: null,
      swarmProgress: undefined,  // Clear swarm progress on error
      pendingOAuth: prev.pendingOAuth  // Preserve pending OAuth on error
    }))

    // Reset refs on error
    streamingStartedRef.current = false
    streamingIdRef.current = null
    completeProcessedRef.current = false
    metadataTracking.reset()

    // Reset swarm mode state on error
    if (swarmModeRef.current.isActive) {
      console.log('[Swarm] Reset due to error')
      swarmModeRef.current = { isActive: false, nodeHistory: [], agentSteps: [] }
    }
  }, [uiState, setMessages, setUIState, setSessionState, streamingStartedRef, streamingIdRef, completeProcessedRef, metadataTracking, textBuffer, stopPollingRef, consumeAssistantTurnBoundary])

  const handleInterruptEvent = useCallback((data: CustomEvent) => {
    const ev = (data as any).value
    if (stopPollingRef.current) {
      stopPollingRef.current()
    }

    setSessionState(prev => ({
      ...prev,
      interrupt: {
        interrupts: ev.interrupts
      }
    }))

    // For A2A tool interrupts (research plan approval), keep current agentStatus
    // to avoid flickering from rapid researching → idle → researching transitions.
    const isA2AInterrupt = ev.interrupts?.some(
      (int: any) => int.reason?.tool_name === 'research_agent'
    )

    setUIState(prev => ({
      ...prev,
      isTyping: false,
      ...(isA2AInterrupt ? {} : { agentStatus: 'idle' })
    }))
  }, [setSessionState, setUIState, stopPollingRef])

  // Handles CustomEvent(name='stream_stopped') emitted by the backend when the user stops generation.
  // RunFinishedEvent (handled by handleCompleteEvent) already sets isTyping/agentStatus/isStreaming,
  // so this handler only needs to mark incomplete tool executions as cancelled.
  const handleStreamStoppedEvent = useCallback(() => {
    startTransition(() => {
      setMessages(prevMsgs => prevMsgs.map(msg => {
        if (msg.isToolMessage && msg.toolExecutions) {
          const updatedToolExecutions = msg.toolExecutions.map(tool =>
            !tool.isComplete ? { ...tool, isComplete: true, isCancelled: true } : tool
          )
          return { ...msg, toolExecutions: updatedToolExecutions }
        }
        return msg
      }))

      setSessionState(prev => ({
        ...prev,
        toolExecutions: prev.toolExecutions.map(te =>
          !te.isComplete ? { ...te, isComplete: true, isCancelled: true } : te
        ),
      }))
    })
  }, [setMessages, setSessionState])

  const handleBrowserProgressEvent = useCallback((event: CustomEvent) => {
    const ev = (event as any).value
    // Append browser step to sessionState
    setSessionState(prev => ({
      ...prev,
      browserProgress: [
        ...(prev.browserProgress || []),
        {
          stepNumber: ev.stepNumber,
          content: ev.content
        }
      ]
    }))
  }, [setSessionState])

  const handleResearchProgressEvent = useCallback((event: CustomEvent) => {
    const ev = (event as any).value
    // Update research progress in sessionState (replace previous status)
    setSessionState(prev => ({
      ...prev,
      researchProgress: {
        stepNumber: ev.stepNumber,
        content: ev.content
      }
    }))
  }, [setSessionState])

  // OAuth Elicitation event handler (MCP elicit_url protocol)
  const handleOAuthElicitationEvent = useCallback((data: CustomEvent) => {
    const ev = (data as any).value
    const serviceName = ev.message?.match(/^(\w+)\s+authorization/i)?.[1] || 'Service'

    console.log(`[OAuth Elicitation] Authorization required for ${serviceName}:`, ev.authUrl)

    setSessionState(prev => ({
      ...prev,
      pendingOAuth: {
        authUrl: ev.authUrl,
        serviceName,
        popupOpened: false,
        elicitationId: ev.elicitationId,
      }
    }))
  }, [setSessionState])

  // Backend settled the elicitation (completed or timed out) — dismiss the
  // dialog. The completion itself was signalled popup -> BFF -> DynamoDB, so
  // this stream event is the only popup-independent way to update parent UI.
  const handleOAuthElicitationResolvedEvent = useCallback((data: CustomEvent) => {
    const ev = (data as any).value
    console.log(`[OAuth Elicitation] Resolved (${ev.status}):`, ev.elicitationId)
    setSessionState(prev => {
      if (prev.pendingOAuth && prev.pendingOAuth.elicitationId !== ev.elicitationId) return prev
      return { ...prev, pendingOAuth: null }
    })
  }, [setSessionState])

  // Swarm Mode event handlers
  const isCodeAgentExec = (t: ToolExecution) =>
    t.toolName === 'code_agent' ||
    t.toolName === 'agentcore_code-agent'

  const handleCodeAgentStartedEvent = useCallback((_event: CustomEvent) => {
    setSessionState(prev => ({
      ...prev,
      codeProgress: [
        { stepNumber: 0, content: 'Code agent started — exploring workspace...' }
      ]
    }))
  }, [setSessionState])

  const handleCodeAgentHeartbeatEvent = useCallback((event: CustomEvent) => {
    const ev = (event as any).value
    const elapsed = ev?.elapsed_seconds || 0
    setSessionState(prev => {
      const existing = prev.codeProgress || []
      // Overwrite the last heartbeat entry instead of appending
      const lastIdx = existing.length - 1
      if (lastIdx >= 0 && existing[lastIdx].content.startsWith('Working...')) {
        const updated = [...existing]
        updated[lastIdx] = { stepNumber: 0, content: `Working... (${elapsed}s elapsed)` }
        return { ...prev, codeProgress: updated }
      }
      return { ...prev, codeProgress: [...existing, { stepNumber: 0, content: `Working... (${elapsed}s elapsed)` }] }
    })
  }, [setSessionState])

  const handleCodeStepEvent = useCallback((event: CustomEvent) => {
    const ev = (event as any).value
    const step = { stepNumber: ev.stepNumber || 0, content: ev.content || '' }

    // 1. Update ephemeral codeProgress for live rendering
    setSessionState(prev => ({
      ...prev,
      codeProgress: [...(prev.codeProgress || []), step],
    }))

    // 2. Persist to toolExecution.codeSteps (survives reconnect + used after completion)
    const activeExec = currentToolExecutionsRef.current.find(
      t => isCodeAgentExec(t) && !t.isComplete
    )
    if (!activeExec) {
      console.warn('[useStreamEvents] code_step: no active code agent exec found for codeSteps persistence')
    }
    if (activeExec) {
      const updatedSteps = [...(activeExec.codeSteps || []), step]
      const updatedExecutions = currentToolExecutionsRef.current.map(t =>
        t.id === activeExec.id ? { ...t, codeSteps: updatedSteps } : t
      )
      currentToolExecutionsRef.current = updatedExecutions
      setSessionState(prev => ({ ...prev, toolExecutions: updatedExecutions }))
      setMessages(prev => prev.map(msg =>
        msg.isToolMessage && msg.toolExecutions
          ? { ...msg, toolExecutions: msg.toolExecutions.map(t =>
              t.id === activeExec.id ? { ...t, codeSteps: updatedSteps } : t
            )}
          : msg
      ))
    }
  }, [setSessionState, currentToolExecutionsRef, setMessages])

  const handleCodeTodoUpdateEvent = useCallback((event: CustomEvent) => {
    const ev = (event as any).value
    const allExecs = currentToolExecutionsRef.current
    const activeExec = allExecs.find(
      t => isCodeAgentExec(t) && !t.isComplete
    )
    if (!activeExec) {
      console.warn('[useStreamEvents] code_todo_update: no active code agent exec found, skipping')
      return
    }

    const todos = ev.todos || []
    const updatedExecutions = currentToolExecutionsRef.current.map(t =>
      t.id === activeExec.id ? { ...t, codeTodos: todos } : t
    )
    currentToolExecutionsRef.current = updatedExecutions
    setSessionState(prev => ({ ...prev, toolExecutions: updatedExecutions }))
    setMessages(prev => prev.map(msg =>
      msg.isToolMessage && msg.toolExecutions
        ? { ...msg, toolExecutions: msg.toolExecutions.map(t =>
            t.id === activeExec.id ? { ...t, codeTodos: todos } : t
          )}
        : msg
    ))
  }, [currentToolExecutionsRef, setSessionState, setMessages])

  const handleCodeResultMetaEvent = useCallback((event: CustomEvent) => {
    const ev = (event as any).value
    const codeExec = currentToolExecutionsRef.current.find(t => isCodeAgentExec(t))
    if (!codeExec) return

    const meta = {
      files_changed: ev.files_changed || [],
      todos: ev.todos || [],
      steps: ev.steps || 0,
    }
    const updatedExecutions = currentToolExecutionsRef.current.map(t =>
      t.id === codeExec.id ? { ...t, codeResultMeta: meta } : t
    )
    currentToolExecutionsRef.current = updatedExecutions
    // Clear codeProgress — live terminal disappears when tool result arrives
    setSessionState(prev => ({ ...prev, toolExecutions: updatedExecutions, codeProgress: undefined }))
    setMessages(prev => prev.map(msg =>
      msg.isToolMessage && msg.toolExecutions
        ? { ...msg, toolExecutions: msg.toolExecutions.map(t =>
            t.id === codeExec.id ? { ...t, codeResultMeta: meta } : t
          )}
        : msg
    ))
  }, [currentToolExecutionsRef, setSessionState, setMessages])

  const handleSwarmNodeStartEvent = useCallback((event: CustomEvent) => {
    const ev = (event as any).value
    const { node_id, node_description } = ev

    const displayName = SWARM_AGENT_DISPLAY_NAMES[node_id] || node_id

    // Mark previous agent as completed (create new object)
    if (swarmModeRef.current.agentSteps.length > 0) {
      const lastIndex = swarmModeRef.current.agentSteps.length - 1
      const lastStep = swarmModeRef.current.agentSteps[lastIndex]
      if (lastStep.status === 'running') {
        swarmModeRef.current.agentSteps[lastIndex] = {
          ...lastStep,
          status: 'completed',
          endTime: Date.now()
        }
      }
    }

    // Add new agent step
    const newStep = {
      nodeId: node_id,
      displayName,
      description: node_description,
      startTime: Date.now(),
      toolCalls: [],
      status: 'running' as const
    }

    // Initialize swarm mode on first node start
    if (!swarmModeRef.current.isActive) {
      swarmModeRef.current.isActive = true
      swarmModeRef.current.nodeHistory = [node_id]
      swarmModeRef.current.agentSteps = [newStep]
      console.log('[Swarm] Started - first node:', node_id)
    } else {
      swarmModeRef.current.nodeHistory = [...swarmModeRef.current.nodeHistory, node_id]
      swarmModeRef.current.agentSteps = [...swarmModeRef.current.agentSteps, newStep]
      console.log('[Swarm] Node started:', node_id)
    }

    // For nodes after first node, reset streaming state
    // (intermediate agents don't create chat messages - text goes to agentStep.responseText)
    if (swarmModeRef.current.nodeHistory.length > 1) {
      textBuffer.reset()
      streamingStartedRef.current = false
      streamingIdRef.current = null
    }

    // Don't add node badge messages - only show progress in SwarmProgress component

    // Update swarm progress in session state
    // Use flushSync only for first node to show SwarmProgress immediately
    // For subsequent nodes (especially responder), avoid flushSync to prevent re-render flash
    const updateSwarmProgress = () => {
      setSessionState(prev => ({
        ...prev,
        swarmProgress: {
          isActive: true,
          currentNode: node_id,
          currentNodeDescription: node_description || '',
          nodeHistory: [...swarmModeRef.current.nodeHistory],
          status: 'running',
          currentAction: `${displayName} working...`,
          agentSteps: [...swarmModeRef.current.agentSteps]
        }
      }))
    }

    if (swarmModeRef.current.nodeHistory.length === 1) {
      // First node - use flushSync for immediate UI feedback
      flushSync(updateSwarmProgress)
    } else {
      // Subsequent nodes - normal state update to avoid re-render flash
      updateSwarmProgress()
    }

    // Debug: log all agent steps
    console.log('[Swarm] Current agentSteps:', JSON.stringify(swarmModeRef.current.agentSteps.map(s => ({
      nodeId: s.nodeId,
      status: s.status,
      hasResponseText: !!s.responseText,
      hasHandoffMessage: !!s.handoffMessage,
      hasHandoffContext: !!s.handoffContext
    }))))

    // Update agent status to swarm
    setUIState(prev => {
      if (prev.agentStatus !== 'swarm' && prev.agentStatus !== 'stopping') {
        return { ...prev, isTyping: true, agentStatus: 'swarm' }
      }
      return prev
    })
  }, [setSessionState, setUIState, setMessages, textBuffer])

  const handleSwarmNodeStopEvent = useCallback((event: CustomEvent) => {
    const ev = (event as any).value
    const { node_id, status } = ev
    console.log('[Swarm] Node stopped:', node_id, 'status:', status)

    // Find and update the agent step with the correct status
    if (swarmModeRef.current.agentSteps.length > 0) {
      const stepIndex = swarmModeRef.current.agentSteps.findIndex(
        step => step.nodeId === node_id && step.status === 'running'
      )
      if (stepIndex >= 0) {
        const currentStep = swarmModeRef.current.agentSteps[stepIndex]
        const finalStatus = status === 'completed' ? 'completed' : 'failed'
        swarmModeRef.current.agentSteps[stepIndex] = {
          ...currentStep,
          status: finalStatus,
          endTime: Date.now()
        }

        // Update session state
        setSessionState(prev => ({
          ...prev,
          swarmProgress: prev.swarmProgress ? {
            ...prev.swarmProgress,
            agentSteps: [...swarmModeRef.current.agentSteps]
          } : prev.swarmProgress
        }))
      }
    }
  }, [setSessionState])

  const handleSwarmHandoffEvent = useCallback((event: CustomEvent) => {
    const ev = (event as any).value
    console.log('[Swarm] Handoff:', ev.from_node, '->', ev.to_node, 'message:', ev.message)

    const toDisplayName = SWARM_AGENT_DISPLAY_NAMES[ev.to_node] || ev.to_node

    // Flush buffer before handoff
    textBuffer.reset()

    // Finalize current streaming message
    if (streamingStartedRef.current && streamingIdRef.current) {
      setMessages(prevMsgs => prevMsgs.map(msg =>
        msg.id === streamingIdRef.current
          ? { ...msg, isStreaming: false }
          : msg
      ))
    }

    // Save handoff message and context to the from_node's step
    if (swarmModeRef.current.agentSteps.length > 0 && ev.from_node) {
      // Find the step for the agent that is handing off
      const stepIndex = swarmModeRef.current.agentSteps.findIndex(
        step => step.nodeId === ev.from_node
      )
      if (stepIndex >= 0) {
        const currentStep = swarmModeRef.current.agentSteps[stepIndex]
        swarmModeRef.current.agentSteps[stepIndex] = {
          ...currentStep,
          ...(ev.message && { handoffMessage: ev.message }),
          ...(ev.context && { handoffContext: ev.context })
        }

        // Log context for debugging
        console.log('[Swarm] Handoff context saved to', ev.from_node, ':', ev.context)
      } else {
        console.warn('[Swarm] Could not find step for from_node:', ev.from_node)
      }
    }

    // Update swarm progress to show handoff (no flushSync - avoid full re-render)
    setSessionState(prev => ({
      ...prev,
      swarmProgress: prev.swarmProgress ? {
        ...prev.swarmProgress,
        currentAction: `Handing off to ${toDisplayName}...`,
        agentSteps: [...swarmModeRef.current.agentSteps]
      } : prev.swarmProgress
    }))

    // Reset streaming refs for next agent
    streamingStartedRef.current = false
    streamingIdRef.current = null
  }, [setMessages, setSessionState, textBuffer])

  const handleSwarmCompleteEvent = useCallback((event: CustomEvent) => {
    const ev = (event as any).value
    console.log('[Swarm] Complete:', ev.total_nodes, 'nodes, status:', ev.status)

    // Mark final agent as completed (create new object)
    if (swarmModeRef.current.agentSteps.length > 0) {
      const lastIndex = swarmModeRef.current.agentSteps.length - 1
      const lastStep = swarmModeRef.current.agentSteps[lastIndex]
      if (lastStep.status === 'running') {
        swarmModeRef.current.agentSteps[lastIndex] = {
          ...lastStep,
          status: 'completed',
          endTime: Date.now()
        }
      }
    }

    // Flush any remaining text
    textBuffer.reset()

    // Build swarmContext for the message (agents used, excluding coordinator/responder)
    const agentsUsed = (ev.node_history || []).filter(
      (n: string) => n !== 'coordinator' && n !== 'responder'
    )
    const swarmContext = agentsUsed.length > 0
      ? { agentsUsed, sharedContext: ev.shared_context }
      : undefined

    // Check if final response came from a non-responder agent (coordinator or specialist)
    const isNonResponderFinal = ev.final_node_id && ev.final_node_id !== 'responder'

    // Reset streaming refs
    streamingStartedRef.current = false
    streamingIdRef.current = null

    // Handle message creation/update
    setMessages(prevMsgs => {
      // If a non-responder agent completed the swarm, create a new message from final_response
      if (isNonResponderFinal && ev.final_response) {
        console.log('[Swarm] Creating message from non-responder final:', ev.final_node_id)
        return [...prevMsgs, {
          id: String(Date.now()),
          text: ev.final_response,
          sender: 'bot' as const,
          timestamp: new Date().toISOString(),
          isStreaming: false,
          images: [],
          swarmContext
        }]
      }

      // Responder case: find and update the last bot message (from current turn)
      const lastBotIdx = prevMsgs.map(m => m.sender).lastIndexOf('bot')
      if (lastBotIdx === -1) {
        // No bot message exists - create fallback if there's a final_response
        if (ev.final_response) {
          console.log('[Swarm] Creating fallback message (no bot message found)')
          return [...prevMsgs, {
            id: String(Date.now()),
            text: ev.final_response,
            sender: 'bot' as const,
            timestamp: new Date().toISOString(),
            isStreaming: false,
            images: [],
            swarmContext
          }]
        }
        return prevMsgs
      }

      // Update the last bot message: finalize streaming and add swarmContext
      return prevMsgs.map((msg, idx) => {
        if (idx === lastBotIdx) {
          return {
            ...msg,
            isStreaming: false,
            swarmContext
          }
        }
        return msg
      })
    })

    // Save final agent steps before reset
    const finalAgentSteps = [...swarmModeRef.current.agentSteps]
    const finalNodeHistory = [...(ev.node_history || swarmModeRef.current.nodeHistory)]

    // Reset swarm mode
    swarmModeRef.current = { isActive: false, nodeHistory: [], agentSteps: [] }

    // Set swarm progress to completed (keeps component visible but collapsed)
    setSessionState(prev => ({
      ...prev,
      swarmProgress: {
        isActive: false,
        currentNode: '',
        currentNodeDescription: '',
        nodeHistory: finalNodeHistory,
        status: ev.status === 'completed' ? 'completed' : 'failed',
        currentAction: undefined,
        agentSteps: finalAgentSteps
      }
    }))
  }, [setSessionState, setMessages, textBuffer])

  const handleStreamEvent = useCallback((event: AGUIStreamEvent) => {
    try {
      // Event deduplication for SSE reconnection
      // Use "eventId:type" as key because multiple event types can share the same SSE event id
      const eventId = (event as any)._eventId as number | undefined
      if (eventId && eventId > 0) {
        const dedupKey = `${eventId}:${event.type}`
        if (processedEventIdsRef.current.has(dedupKey)) {
          return  // Skip duplicate
        }
        processedEventIdsRef.current.add(dedupKey)
      }

      switch (event.type) {
        case EventType.RUN_STARTED:
          handleInitEvent()
          break
        case EventType.TEXT_MESSAGE_START:
          handleTextMessageStartEvent(event)
          break
        case EventType.TEXT_MESSAGE_CONTENT:
          handleTextMessageContentEvent(event)
          break
        case EventType.TEXT_MESSAGE_END:
          handleTextMessageEndEvent(event)
          break
        case EventType.TOOL_CALL_START:
          handleToolCallStartEvent(event)
          break
        case EventType.TOOL_CALL_ARGS:
          handleToolCallArgsEvent(event)
          break
        case EventType.TOOL_CALL_END:
          handleToolCallEndEvent(event)
          break
        case EventType.TOOL_CALL_RESULT:
          handleToolCallResultEvent(event)
          break
        case EventType.RUN_FINISHED:
          // Return the cleanup promise so buffered background executions can
          // serialize complete runs. Live stream callers may ignore it.
          return handleCompleteEvent(event).catch(err => {
            console.error('[useStreamEvents] Error in complete event handler:', err)
          })
        case EventType.RUN_ERROR:
          handleErrorEvent(event)
          break
        case EventType.CUSTOM: {
          const customEvent = event
          switch (customEvent.name) {
            case 'reasoning':
              handleReasoningEvent(customEvent)
              break
            case 'interrupt':
              handleInterruptEvent(customEvent)
              break
            case 'warning': {
              // Show warning as a bot message without stopping the stream
              const turnBoundary = consumeAssistantTurnBoundary()
              setMessages(prev => [...prev, {
                id: `warning_${Date.now()}`,
                text: `⚠️ ${(customEvent as any).value?.message}`,
                sender: 'bot',
                timestamp: new Date().toISOString(),
                ...turnBoundary,
              }])
              break
            }
            case 'browser_progress':
              handleBrowserProgressEvent(customEvent)
              break
            case 'research_progress':
              handleResearchProgressEvent(customEvent)
              break
            case 'oauth_elicitation':
              handleOAuthElicitationEvent(customEvent)
              break
            case 'oauth_elicitation_resolved':
              handleOAuthElicitationResolvedEvent(customEvent)
              break
            case 'code_agent_started':
              handleCodeAgentStartedEvent(customEvent)
              break
            case 'code_agent_heartbeat':
              handleCodeAgentHeartbeatEvent(customEvent)
              break
            case 'code_step':
              handleCodeStepEvent(customEvent)
              break
            case 'code_todo_update':
              handleCodeTodoUpdateEvent(customEvent)
              break
            case 'code_result_meta':
              handleCodeResultMetaEvent(customEvent)
              break
            case 'swarm_node_start':
              handleSwarmNodeStartEvent(customEvent)
              break
            case 'swarm_node_stop':
              handleSwarmNodeStopEvent(customEvent)
              break
            case 'swarm_handoff':
              handleSwarmHandoffEvent(customEvent)
              break
            case 'swarm_complete':
              handleSwarmCompleteEvent(customEvent)
              break
            case 'metadata': {
              const ev = (customEvent as any).value
              if (ev?.browserSessionId) {
                setSessionState(prev => {
                  if (prev.browserSession) return prev
                  return { ...prev, browserSession: { sessionId: ev.browserSessionId, browserId: ev.browserId || null } } as ChatSessionState
                })
                if (!sessionState.browserSession && onBrowserSessionDetected) {
                  onBrowserSessionDetected(ev.browserSessionId, ev.browserId || '')
                }
              }
              break
            }
            case 'stream_stopped':
              handleStreamStoppedEvent()
              break
            case 'complete_metadata': {
              // Carries token usage (and optional images) from the AG-UI complete event
              const meta = (customEvent as any).value
              if (meta?.usage) {
                tokenUsageRef.current = meta.usage as TokenUsage
              }
              break
            }
            case 'progress':
              // no-op
              break
          }
          break
        }
      }
    } catch (error) {
      console.error('[useStreamEvents] Error processing stream event:', error, 'Event type:', event?.type)
    }
  }, [
    handleReasoningEvent,
    handleTextMessageStartEvent,
    handleTextMessageContentEvent,
    handleTextMessageEndEvent,
    handleToolCallStartEvent,
    handleToolCallArgsEvent,
    handleToolCallEndEvent,
    handleToolCallResultEvent,
    handleCompleteEvent,
    handleInitEvent,
    handleErrorEvent,
    handleInterruptEvent,
    handleBrowserProgressEvent,
    handleResearchProgressEvent,
    handleOAuthElicitationEvent,
    handleOAuthElicitationResolvedEvent,
    handleCodeAgentStartedEvent,
    handleCodeAgentHeartbeatEvent,
    handleCodeStepEvent,
    handleCodeTodoUpdateEvent,
    handleCodeResultMetaEvent,
    handleSwarmNodeStartEvent,
    handleSwarmNodeStopEvent,
    handleSwarmHandoffEvent,
    handleSwarmCompleteEvent,
    handleStreamStoppedEvent,
    consumeAssistantTurnBoundary,
    setSessionState,
    setMessages,
    onBrowserSessionDetected
  ])

  // Reset streaming state (called when user stops generation)
  const resetStreamingState = useCallback(() => {
    // Flush any remaining buffered text before resetting
    textBuffer.reset()

    streamingStartedRef.current = false
    streamingIdRef.current = null
    completeProcessedRef.current = false
    tokenUsageRef.current = null
    pendingAssistantTurnBoundaryRef.current = false
    processedEventIdsRef.current.clear()
    metadataTracking.reset()

    // Reset swarm mode state if active
    if (swarmModeRef.current.isActive) {
      console.log('[Swarm] Reset during streaming stop')
      swarmModeRef.current = { isActive: false, nodeHistory: [], agentSteps: [] }
    }

    // Mark streaming message as stopped and cancel any in-progress tool executions
    setMessages(prev => prev.map(msg => {
      if (msg.isStreaming) return { ...msg, isStreaming: false }
      if (msg.isToolMessage && msg.toolExecutions) {
        const updated = msg.toolExecutions.map(te =>
          !te.isComplete && !te.isCancelled
            ? { ...te, isCancelled: true }
            : te
        )
        return { ...msg, toolExecutions: updated }
      }
      return msg
    }))

    setSessionState(prev => ({
      ...prev,
      reasoning: null,
      streaming: null,
      swarmProgress: undefined
    }))

    // Reset UI — covers the case where stream was aborted without receiving RunFinishedEvent
    setUIState(prev => ({ ...prev, agentStatus: 'idle', isTyping: false }))
  }, [setMessages, setSessionState, setUIState, metadataTracking, textBuffer])

  return { handleStreamEvent, resetStreamingState }
}
