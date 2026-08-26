/**
 * useVoiceChat Hook (Refactored)
 *
 * Manages WebSocket communication and audio I/O for voice chat.
 * State management is delegated to the parent (useChat) via callbacks.
 *
 * Architecture:
 * 1. BFF /api/voice/start - Initialize session, get WebSocket URL
 * 2. WebSocket → AgentCore - Audio streaming only
 * 3. BFF /api/voice/end - Update session metadata on disconnect
 */

import { useRef, useCallback, useEffect, useState } from 'react'
import {
  AudioRecorder,
  AudioPlayer,
  AudioChunk,
  checkAudioSupport,
  AUDIO_CONFIG,
} from '@/lib/audioUtils'
import { fetchAuthSession } from 'aws-amplify/auth'
import { AgentStatus } from '@/types/events'
import { getRuntimeAccessToken } from '@/lib/runtime-auth'

function toBase64Url(value: string): string {
  return btoa(value)
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '')
}

// Transcript entry
export interface TranscriptEntry {
  id: string
  role: 'user' | 'assistant'
  text: string
  timestamp: number
  isFinal: boolean
}

// Pending transcript for real-time display
export interface PendingTranscript {
  role: 'user' | 'assistant'
  text: string
  isActive: boolean
}

// Tool execution in voice mode
export interface VoiceToolExecution {
  toolUseId: string
  toolName: string
  status: 'running' | 'complete' | 'error'
  input?: any
  result?: string
}

// Token usage statistics from voice conversation
export interface VoiceUsageStats {
  inputTokens: number
  outputTokens: number
  totalTokens: number
}

export interface UseVoiceChatProps {
  sessionId: string | null
  userId?: string
  enabledTools?: string[]
  // Callbacks for state management (delegated to useChat)
  onStatusChange: (status: AgentStatus) => void
  onTranscript?: (entry: TranscriptEntry) => void
  onToolExecution?: (execution: VoiceToolExecution) => void
  onResponseComplete?: () => void  // Called when assistant finishes speaking (to finalize message)
  onUsageUpdate?: (usage: VoiceUsageStats) => void  // Token usage statistics
  onError?: (error: Error) => void
  onSessionCreated?: () => void  // Called when new session is created (to refresh session list)
}

export interface UseVoiceChatReturn {
  // Connection state (internal only)
  isConnected: boolean
  isSupported: boolean
  missingFeatures: string[]
  error: string | null

  // Tool execution state
  currentToolExecution: VoiceToolExecution | null

  // Real-time transcript (for live display before final)
  pendingTranscript: PendingTranscript | null

  // Actions
  connect: () => Promise<void>
  disconnect: () => void

  // Audio controls
  setVolume: (volume: number) => void
}

export function useVoiceChat({
  sessionId,
  userId,
  enabledTools = [],
  onStatusChange,
  onTranscript,
  onToolExecution,
  onResponseComplete,
  onUsageUpdate,
  onError,
  onSessionCreated,
}: UseVoiceChatProps): UseVoiceChatReturn {
  // ==================== STATE ====================
  const [isConnected, setIsConnected] = useState(false)
  const [currentToolExecution, setCurrentToolExecution] = useState<VoiceToolExecution | null>(null)
  const [pendingTranscript, setPendingTranscript] = useState<PendingTranscript | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isClient, setIsClient] = useState(false)

  // Hydration-safe: only check audio support after client mount
  useEffect(() => {
    setIsClient(true)
  }, [])

  // Audio support - only evaluated on client
  const { supported: isSupported, missing: missingFeatures } = isClient
    ? checkAudioSupport()
    : { supported: false, missing: [] }

  // ==================== REFS ====================
  const wsRef = useRef<WebSocket | null>(null)
  const recorderRef = useRef<AudioRecorder | null>(null)
  const playerRef = useRef<AudioPlayer | null>(null)
  const reconnectAttemptRef = useRef(0)
  const maxReconnectAttempts = 3
  const messageCountRef = useRef(0)
  const activeSessionIdRef = useRef<string | null>(null)

  // Idle timeout - auto-disconnect after 60 seconds of silence
  const IDLE_TIMEOUT_MS = 60 * 1000  // 60 seconds
  const idleTimerRef = useRef<NodeJS.Timeout | null>(null)
  const lastActivityRef = useRef<number>(Date.now())

  // Track if user has spoken - ignore assistant messages until first user input
  // This prevents auto-responses when resuming a session with conversation history
  const userHasSpokenRef = useRef<boolean>(false)

  // ==================== IDLE TIMEOUT HANDLERS ====================

  /**
   * Reset idle timer - called on any activity (transcript, audio, etc.)
   */
  const resetIdleTimer = useCallback(() => {
    lastActivityRef.current = Date.now()

    // Clear existing timer
    if (idleTimerRef.current) {
      clearTimeout(idleTimerRef.current)
    }

    // Set new timer
    idleTimerRef.current = setTimeout(() => {
      console.log('[VoiceChat] Idle timeout reached, auto-disconnecting')
      // Disconnect will be called via the disconnect function
      if (wsRef.current) {
        wsRef.current.close(1000, 'Idle timeout')
      }
    }, IDLE_TIMEOUT_MS)
  }, [])

  /**
   * Clear idle timer (on disconnect)
   */
  const clearIdleTimer = useCallback(() => {
    if (idleTimerRef.current) {
      clearTimeout(idleTimerRef.current)
      idleTimerRef.current = null
    }
  }, [])

  // ==================== WEBSOCKET HANDLERS ====================

  /**
   * Send audio chunk to server
   */
  const sendAudioChunk = useCallback((chunk: AudioChunk) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'bidi_audio_input',
        audio: chunk.audio,
        format: 'pcm',
        sample_rate: AUDIO_CONFIG.sampleRate,
        channels: AUDIO_CONFIG.channels,
      }))
    }
  }, [])

  /**
   * Handle incoming WebSocket messages
   */
  // Track if voice session has been initialized (first message received)
  const sessionInitializedRef = useRef(false)

  const handleMessage = useCallback((event: MessageEvent) => {
    try {
      const data = JSON.parse(event.data)

      // Workaround for AgentCore Runtime WebSocket proxy issue:
      // The proxy may drop the initial bidi_connection_start message.
      // Treat ANY first message as connection established.
      if (!sessionInitializedRef.current) {
        sessionInitializedRef.current = true
        onStatusChange('voice_listening')
        // Auto-start recording when connected
        recorderRef.current?.start(sendAudioChunk)
      }

      switch (data.type) {
        case 'bidi_connection_start':
          // Already handled above via first-message detection
          // Keep this case for local mode where bidi_connection_start arrives properly
          break

        case 'bidi_response_start':
          onStatusChange('voice_processing')
          break

        case 'bidi_audio_stream':
          // Play audio from agent
          // Ignore audio if user hasn't spoken yet (prevents auto-play on session resume)
          if (data.audio && playerRef.current && userHasSpokenRef.current) {
            onStatusChange('voice_speaking')
            playerRef.current.playChunk(data.audio)
          }
          break

        case 'bidi_transcript_stream':
          // Handle transcript (user or assistant)
          // Backend sends delta text - we pass it to onTranscript which accumulates
          console.log(`[VoiceChat] Transcript: role=${data.role}, is_final=${data.is_final}, delta="${data.delta?.substring(0, 50) || ''}..."`)

          const role = data.role as 'user' | 'assistant'

          // Track when user first speaks
          if (role === 'user') {
            userHasSpokenRef.current = true
          }

          // Ignore assistant messages until user has spoken
          // This prevents auto-responses when resuming a session with conversation history
          if (role === 'assistant' && !userHasSpokenRef.current) {
            console.log('[VoiceChat] Ignoring assistant message before user input')
            break
          }

          // Reset idle timer on any valid transcript activity
          resetIdleTimer()

          const entry: TranscriptEntry = {
            id: `${role}_${Date.now()}`,
            role: role,
            text: data.delta || '',  // This is now delta, not accumulated text
            timestamp: Date.now(),
            isFinal: data.is_final ?? false,
          }
          // Count final transcripts for session metadata
          if (data.is_final) {
            messageCountRef.current++
          }
          onTranscript?.(entry)
          break

        case 'bidi_interruption':
          // User interrupted assistant - finalize current assistant message and clear audio
          console.log('[VoiceChat] User interrupted - finalizing assistant message')
          playerRef.current?.clear()
          onResponseComplete?.()  // Finalize streaming assistant message
          onStatusChange('voice_listening')
          break

        case 'bidi_response_complete':
          // Agent finished speaking - finalize the assistant message
          console.log('[VoiceChat] Response complete - finalizing assistant turn')
          onResponseComplete?.()
          onStatusChange('voice_listening')
          break

        case 'tool_use':
        case 'tool_use_stream':
          // Tool execution started - finalize current assistant message first
          // Note: tool_use_stream is alternative event name used by some implementations
          // tool_use_stream format: { type, current_tool_use: { toolUseId, name, input } }
          // tool_use format: { type, toolUseId, name, input }
          console.log('[VoiceChat] Tool use event received:', data)
          // Signal to finalize current assistant message before tool execution
          // This ensures text before tool use is saved as a separate message
          onResponseComplete?.()

          // Handle both formats (tool_use_stream wraps in current_tool_use)
          const toolData = data.current_tool_use || data
          const toolStart: VoiceToolExecution = {
            toolUseId: toolData.toolUseId,
            toolName: toolData.name,
            status: 'running',
            input: toolData.input,
          }
          console.log('[VoiceChat] Created toolStart:', toolStart)
          setCurrentToolExecution(toolStart)
          onToolExecution?.(toolStart)
          break

        case 'tool_result':
          // Tool execution completed
          setCurrentToolExecution((prev) => {
            if (prev && prev.toolUseId === data.toolUseId) {
              const toolComplete: VoiceToolExecution = {
                toolUseId: prev.toolUseId,
                toolName: prev.toolName,
                status: data.status === 'error' ? 'error' : 'complete',
                input: prev.input,
                result: data.content,
              }
              onToolExecution?.(toolComplete)
              return toolComplete
            }
            return prev
          })
          // Clear after a short delay
          setTimeout(() => setCurrentToolExecution(null), 2000)
          break

        case 'bidi_connection_restart':
          // Nova Sonic has 8-minute session timeout, auto-reconnects
          // Just log and continue - connection restarts automatically
          console.log('[VoiceChat] Connection restarting (8-min timeout)')
          onStatusChange('voice_connecting')
          break

        case 'bidi_error':
          console.error('[VoiceChat] Server error:', data.message)
          setError(data.message)
          // Recoverable errors don't disconnect - just notify user
          if (data.recoverable) {
            console.log('[VoiceChat] Recoverable error, staying connected')
            onStatusChange('voice_listening')
          } else {
            onStatusChange('idle')
          }
          onError?.(new Error(data.message))
          break

        case 'bidi_connection_close':
          setIsConnected(false)
          onStatusChange('idle')
          break

        case 'bidi_usage':
          // Token usage statistics (if available from model)
          if (data.inputTokens !== undefined) {
            console.log(`[VoiceChat] Usage: input=${data.inputTokens}, output=${data.outputTokens}, total=${data.totalTokens}`)
            onUsageUpdate?.({
              inputTokens: data.inputTokens,
              outputTokens: data.outputTokens,
              totalTokens: data.totalTokens,
            })
          }
          break

        default:
          // Ignore unknown event types
          break
      }
    } catch (err) {
      console.error('[VoiceChat] Failed to parse message:', err)
    }
  }, [onStatusChange, onTranscript, onToolExecution, onResponseComplete, onUsageUpdate, onError, sendAudioChunk, resetIdleTimer])

  // ==================== ACTIONS ====================

  /**
   * Call BFF to end voice session and update metadata
   */
  const endVoiceSession = useCallback(async (sessionIdToEnd: string) => {
    try {
      // Get auth headers
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      }
      try {
        const session = await fetchAuthSession()
        const token = session.tokens?.accessToken?.toString()
        if (token) {
          headers['Authorization'] = `Bearer ${token}`
        }
      } catch {
        // Continue without auth
      }

      await fetch('/api/voice/end', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          sessionId: sessionIdToEnd,
          messageCount: messageCountRef.current,
        }),
      })
      console.log('[VoiceChat] Session ended via BFF')
    } catch (err) {
      console.warn('[VoiceChat] Failed to end session via BFF:', err)
    }
  }, [])

  /**
   * Connect to voice chat WebSocket via BFF
   */
  const connect = useCallback(async () => {
    if (!isSupported) {
      setError(`Browser missing required features: ${missingFeatures.join(', ')}`)
      return
    }

    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return
    }

    onStatusChange('voice_connecting')
    setError(null)
    messageCountRef.current = 0

    try {
      // Get auth token once (reused for BFF and WebSocket)
      let authToken: string | null = null
      try {
        authToken = await getRuntimeAccessToken()
      } catch {
        // Continue without auth for local development
      }

      // Ensure we use the same session ID as text mode
      // Priority: 1. sessionId from props (from useChat) 2. sessionStorage 3. null (BFF creates new)
      const effectiveSessionId = sessionId || sessionStorage.getItem('chat-session-id')

      // Build headers for BFF
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      }
      if (effectiveSessionId) {
        headers['X-Session-ID'] = effectiveSessionId
        console.log(`[VoiceChat] Using session ID: ${effectiveSessionId}`)
      } else {
        console.log('[VoiceChat] No session ID available - BFF will create new session')
      }
      if (authToken) {
        headers['Authorization'] = `Bearer ${authToken}`
      }

      // 1. Call BFF to start voice session
      const startResponse = await fetch('/api/voice/start', {
        method: 'POST',
        headers,
        body: JSON.stringify({ enabledTools }),
      })

      if (!startResponse.ok) {
        const errorData = await startResponse.json()
        throw new Error(errorData.error || 'Failed to start voice session')
      }

      const startData = await startResponse.json()
      const { sessionId: activeSessionId, wsUrl, isLocal } = startData

      // Store active session ID for cleanup
      activeSessionIdRef.current = activeSessionId

      // Sync session ID to sessionStorage so text mode uses the same session
      // This ensures voice-text continuity even if voice mode created the session
      const existingSessionId = sessionStorage.getItem('chat-session-id')
      if (!existingSessionId && activeSessionId) {
        sessionStorage.setItem('chat-session-id', activeSessionId)
        console.log(`[VoiceChat] Synced session ID to sessionStorage: ${activeSessionId}`)
        // Notify parent that a new session was created (refresh session list)
        onSessionCreated?.()
      } else if (existingSessionId !== activeSessionId) {
        console.warn(`[VoiceChat] Session ID mismatch - expected: ${existingSessionId}, got: ${activeSessionId}`)
      }

      console.log('[VoiceChat] Connecting to WebSocket (URL from BFF)')

      // Browser WebSocket cannot set Authorization. AgentCore accepts the
      // base64url bearer token through the WebSocket subprotocol header.
      const protocols = authToken
        ? [
            `base64UrlBearerAuthorization.${toBase64Url(authToken)}`,
            'base64UrlBearerAuthorization',
          ]
        : undefined
      const ws = protocols ? new WebSocket(wsUrl, protocols) : new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => {
        reconnectAttemptRef.current = 0
        setIsConnected(true)
        // Reset user spoken flag for new connection
        userHasSpokenRef.current = false
        // Start idle timer
        resetIdleTimer()

        // Keep one application config frame for local mode and dynamic tool
        // settings. Cloud authentication is handled by the handshake.
        ws.send(JSON.stringify({
          type: 'config',
          session_id: activeSessionId,
          user_id: userId,
          enabled_tools: enabledTools,
          ...(isLocal && authToken ? { auth_token: authToken } : {}),
        }))
        console.log(`[VoiceChat] Sent config: session=${activeSessionId}, tools=${enabledTools.length}`)
      }

      ws.onmessage = handleMessage

      ws.onerror = (event) => {
        console.error('[VoiceChat] WebSocket error:', event)
        setError('Connection error')
        onStatusChange('idle')
      }

      ws.onclose = (event) => {
        wsRef.current = null
        setIsConnected(false)

        // Call BFF to end session (update metadata)
        if (activeSessionIdRef.current) {
          endVoiceSession(activeSessionIdRef.current)
          activeSessionIdRef.current = null
        }

        // Attempt reconnect if not intentional close
        if (event.code !== 1000 && reconnectAttemptRef.current < maxReconnectAttempts) {
          reconnectAttemptRef.current++
          setTimeout(() => connect(), 1000 * reconnectAttemptRef.current)
        } else {
          onStatusChange('idle')
        }
      }

      // Initialize audio components
      if (!recorderRef.current) {
        recorderRef.current = new AudioRecorder()
      }
      if (!playerRef.current) {
        playerRef.current = new AudioPlayer()
      }

      await recorderRef.current.initialize()
      await playerRef.current.initialize()

    } catch (err) {
      console.error('[VoiceChat] Connection failed:', err)
      setError(err instanceof Error ? err.message : 'Connection failed')
      onStatusChange('idle')
      onError?.(err instanceof Error ? err : new Error('Connection failed'))
    }
  }, [sessionId, enabledTools, isSupported, missingFeatures, handleMessage, onStatusChange, onError, endVoiceSession, resetIdleTimer, onSessionCreated])

  /**
   * Disconnect from voice chat
   */
  const disconnect = useCallback(() => {
    // Clear idle timer
    clearIdleTimer()

    // Stop recording
    recorderRef.current?.stop()

    // Clear audio playback queue immediately (stop any playing audio)
    playerRef.current?.clear()

    // Reset session initialized flag for next connection
    sessionInitializedRef.current = false

    // Close WebSocket
    if (wsRef.current) {
      wsRef.current.close(1000, 'User disconnected')
      wsRef.current = null
    }

    setIsConnected(false)
    onStatusChange('idle')
  }, [onStatusChange, clearIdleTimer])

  /**
   * Set playback volume
   */
  const setVolume = useCallback((volume: number) => {
    playerRef.current?.setVolume(volume)
  }, [])

  // ==================== SESSION CHANGE DETECTION ====================
  // Disconnect voice chat when session changes (e.g., user switches to another session)
  // Also track the session ID that was used to establish the current voice connection
  const connectedSessionIdRef = useRef<string | null>(null)

  // Track which session ID was used when voice connection was established
  useEffect(() => {
    if (isConnected && sessionId) {
      // Store the session ID when we become connected
      if (!connectedSessionIdRef.current) {
        connectedSessionIdRef.current = sessionId
      }
    } else if (!isConnected) {
      // Clear when disconnected
      connectedSessionIdRef.current = null
    }
  }, [isConnected, sessionId])

  // Disconnect if session changes while voice is active
  useEffect(() => {
    // Only disconnect if:
    // 1. We have an active WebSocket connection
    // 2. We had a connected session ID
    // 3. The session ID actually changed (not just from null to value)
    if (wsRef.current && connectedSessionIdRef.current && sessionId !== connectedSessionIdRef.current) {
      console.log(`[VoiceChat] Session changed from ${connectedSessionIdRef.current} to ${sessionId}, disconnecting`)
      disconnect()
    }
  }, [sessionId, disconnect])

  // ==================== CLEANUP ====================
  useEffect(() => {
    return () => {
      clearIdleTimer()
      recorderRef.current?.stop()
      recorderRef.current?.dispose()
      playerRef.current?.dispose()
      sessionInitializedRef.current = false
      if (wsRef.current) {
        wsRef.current.close(1000, 'Component unmounted')
        wsRef.current = null
      }
    }
  }, [clearIdleTimer])

  // ==================== RETURN ====================
  return {
    // Connection state
    isConnected,
    isSupported,
    missingFeatures,
    error,

    // Tool execution
    currentToolExecution,

    // Real-time transcript
    pendingTranscript,

    // Actions
    connect,
    disconnect,

    // Audio controls
    setVolume,
  }
}
