import { useCallback, useRef, useState } from 'react'
import type { ImagePickerAsset } from 'expo-image-picker'
import { useStreamEvents } from './useStreamEvents'
import { useChatStream } from './useChatStream'
import { useSSEReconnect } from './useSSEReconnect'
import { apiGet, apiPost, apiPut } from '../lib/api-client'
import { ENDPOINTS } from '../lib/constants'
import { makeEmptyMessage } from '../types/chat'
import type { Message } from '../types/chat'
import { useArtifactContext } from '../context/ArtifactContext'
import { useSessionContext } from '../context/SessionContext'
import type { ArtifactSignal } from '../types/artifact'

const PAGE_SIZE = 30

/** If content is an s3:// URL, convert to a presigned URL via BFF. */
async function resolveS3Content(content: string): Promise<string> {
  if (typeof content !== 'string' || !content.startsWith('s3://')) return content
  try {
    const { url } = await apiPost<{ url: string }>(ENDPOINTS.s3PresignedUrl, { s3Key: content })
    return url
  } catch {
    return content
  }
}

export interface UseChatOptions {
  sessionId: string
  modelId?: string
  onTitleUpdated?: (title: string) => void
}

/**
 * Top-level chat orchestrator.
 * Combines useStreamEvents + useChatStream and manages user message insertion.
 */
export function useChat({ sessionId, modelId, onTitleUpdated }: UseChatOptions) {
  const [isSending, setIsSending] = useState(false)
  const [networkError, setNetworkError] = useState<string | null>(null)
  const [displayCount, setDisplayCount] = useState(PAGE_SIZE)
  const allHistoryRef = useRef<Message[]>([])

  const { addArtifact, updateArtifact, artifacts } = useArtifactContext()
  const { activeSessionId } = useSessionContext()
  // Keep a ref to the latest artifacts so signal handlers always see fresh state
  const artifactsRef = useRef(artifacts)
  artifactsRef.current = artifacts

  const handleArtifactSignal = useCallback(
    async (signal: ArtifactSignal) => {
      const sid = activeSessionId

      if (signal.kind === 'excalidraw') {
        const existing = artifactsRef.current.find(
          a => a.type === 'excalidraw' && a.sessionId === sid,
        )
        if (existing) {
          updateArtifact(existing.id, {
            content: signal.data,
            timestamp: new Date().toISOString(),
          })
        } else {
          const newId = `excalidraw-${sid}`
          addArtifact({
            id: newId,
            type: 'excalidraw',
            title: (signal.data as { title?: string }).title ?? 'Diagram',
            content: signal.data,
            timestamp: new Date().toISOString(),
            sessionId: sid,
          })
        }
        return
      }

      if (signal.kind === 'browser_extract') {
        // Try to parse JSON from the tool output
        let content = signal.toolOutput
        let description: string | undefined
        const jsonMatch = content.match(/```(?:json)?\s*([\s\S]+?)```/)
        if (jsonMatch) content = jsonMatch[1].trim()
        // Extract description from markdown if present
        const descMatch = signal.toolOutput.match(/^#+\s+(.+)/m)
        if (descMatch) description = descMatch[1]
        addArtifact({
          id: signal.artifactId,
          type: 'extracted_data',
          title: description ?? 'Extracted Data',
          content,
          description,
          timestamp: new Date().toISOString(),
          sessionId: sid,
          metadata: signal.metadata,
        })
        return
      }

      if (signal.kind === 'run_finished') {
        // Refresh artifacts from the backend (agentcore saves them in agent.state).
        // This avoids duplicating workspace-file-fetch / presigned-URL logic that
        // already lives in the BFF conversation history endpoint.
        try {
          const data = await apiGet<{
            success: boolean
            artifacts?: Array<{
              id: string; type: string; title: string; content: unknown
              description?: string; metadata?: Record<string, unknown>
              created_at?: string; timestamp?: string
            }>
          }>(ENDPOINTS.conversationHistory(sid))

          for (const a of data.artifacts ?? []) {
            // Skip diagram/image artifacts — rendered inline in chat instead
            if (a.type === 'diagram' || a.type === 'image') continue
            let content: string | import('../types/artifact').ExcalidrawData =
              typeof a.content === 'string'
                ? a.content
                : (a.content as import('../types/artifact').ExcalidrawData)
            // Resolve s3:// paths to presigned URLs for image artifacts
            if (typeof content === 'string' && content.startsWith('s3://')) {
              content = await resolveS3Content(content)
            }
            addArtifact({
              id: a.id,
              type: a.type as import('../types/artifact').ArtifactType,
              title: a.title,
              content,
              description: a.description,
              timestamp: a.created_at ?? a.timestamp ?? new Date().toISOString(),
              sessionId: sid,
              metadata: a.metadata as Record<string, unknown> | undefined,
            })
          }
        } catch {
          // non-critical — artifacts will load on next session open
        }
      }
    },
    [activeSessionId, addArtifact, updateArtifact],
  )

  const {
    onStreamStart,
    attemptReconnect,
    reset: resetReconnect,
    isReconnecting,
    reconnectAttempt,
  } = useSSEReconnect()

  const {
    messages,
    setMessages,
    agentStatus,
    thinkingMessage,
    pendingOAuth,
    setPendingOAuth,
    pendingInterrupt,
    setPendingInterrupt,
    handleEvent,
    resetStreamState,
    clearMessages,
  } = useStreamEvents({
    onExecutionMeta: onStreamStart,
    onArtifactSignal: handleArtifactSignal,
  })

  const handleStreamComplete = useCallback(() => {
    setIsSending(false)
    // Sync display messages back to allHistoryRef so next sendMessage has full history
    setMessages(current => {
      allHistoryRef.current = current
      return current
    })
    resetStreamState()
    resetReconnect()
  }, [setMessages, resetStreamState, resetReconnect])

  const handleStreamError = useCallback((err: string) => {
    setIsSending(false)
    attemptReconnect(
      handleEvent,
      () => {
        // Reconnect replay succeeded — finish cleanly
        resetStreamState()
        resetReconnect()
      },
      () => {
        // All reconnect attempts exhausted — surface the original error
        setNetworkError(err)
        resetStreamState()
        resetReconnect()
      },
    )
  }, [attemptReconnect, handleEvent, resetStreamState, resetReconnect])

  const { sendMessage: streamSend, stopStream: rawStopStream, abortStream } = useChatStream({
    sessionId,
    modelId,
    onEvent: handleEvent,
    onError: handleStreamError,
    onComplete: handleStreamComplete,
  })

  // Wrap stopStream to guarantee state reset even if onComplete doesn't fire
  const stopStream = useCallback(async () => {
    const stopped = await rawStopStream()
    if (!stopped) {
      setNetworkError('Unable to stop the active run. Please try again.')
      return
    }
    setIsSending(false)
    resetStreamState()
    resetReconnect()
    // Sync display messages to allHistoryRef
    setMessages(current => {
      allHistoryRef.current = current
      return current
    })
  }, [rawStopStream, setMessages, resetStreamState, resetReconnect])

  const sendMessage = useCallback(
    async (text: string, images?: ImagePickerAsset[], documents?: import('../types/chat').PickedDocument[]) => {
      if (!text.trim() || isSending) return
      setNetworkError(null)
      setIsSending(true)

      // Insert user message immediately
      const userMsg: Message = {
        ...makeEmptyMessage(crypto.randomUUID(), 'user'),
        text: text.trim(),
        images: images?.map(a => ({ type: 'url' as const, url: a.uri, title: a.fileName ?? undefined })),
        uploadedFiles: documents?.map(d => ({ name: d.name, type: d.mimeType })),
      }
      allHistoryRef.current = [...allHistoryRef.current, userMsg]
      setMessages(prev => [...prev, userMsg])

      // On first message, update session title (same truncation logic as BFF)
      if (allHistoryRef.current.length <= 1) {
        const trimmed = text.trim()
        const title = trimmed.length > 50 ? trimmed.substring(0, 47) + '...' : trimmed
        apiPut(ENDPOINTS.sessionById(sessionId), { title })
          .then(() => onTitleUpdated?.(title))
          .catch(() => {/* non-critical */})
      }

      // The history passed to BFF uses full history, not the windowed display slice
      await streamSend(text, allHistoryRef.current, images, documents)
    },
    [isSending, setMessages, streamSend],
  )

  /**
   * Load conversation history for this session from the BFF.
   * Uses /api/conversation/history which returns messages with content blocks.
   */
  const loadHistory = useCallback(async () => {
    console.log('[loadHistory] called for session:', sessionId)
    // Abort any active SSE connection (no server stop — avoids killing a new session's stream)
    abortStream()
    setIsSending(false)
    clearMessages()
    try {
      const data = await apiGet<{
        success: boolean
        artifacts?: Array<{
          id: string
          type: string
          title: string
          content: unknown
          description?: string
          metadata?: Record<string, unknown>
          created_at?: string
          timestamp?: string
        }>
        messages: Array<{
          id: string
          role: string
          content: Array<{
            text?: string
            toolUse?: { toolUseId: string; name: string; input: unknown }
            toolResult?: { toolUseId: string; content: unknown; status: string }
          }>
          timestamp?: string
        }>
      }>(ENDPOINTS.conversationHistory(sessionId))

      if (!data.success || !data.messages) return

      // Build toolResult map for pairing with toolUse
      const toolResultMap = new Map<string, {
        content: unknown; status: string
        images?: import('../types/events').ImageData[]
      }>()
      for (const msg of data.messages) {
        if (!Array.isArray(msg.content)) continue
        for (const item of msg.content) {
          if (item.toolResult) {
            // Extract images from toolResult content array (base64)
            const images: import('../types/events').ImageData[] = []
            if (Array.isArray(item.toolResult.content)) {
              for (const block of item.toolResult.content as Array<Record<string, unknown>>) {
                if (block.image) {
                  const img = block.image as Record<string, unknown>
                  const fmt = (img.format as string) ?? 'png'
                  const src = img.source as Record<string, unknown> | undefined
                  if (src?.bytes) {
                    const bytesObj = src.bytes as Record<string, unknown>
                    const b64 = (bytesObj.data ?? bytesObj.__bytes_encoded__) as string | undefined
                    if (typeof b64 === 'string' && b64.length > 100) {
                      images.push({ format: fmt, data: b64 })
                    }
                  }
                }
              }
            }
            toolResultMap.set(item.toolResult.toolUseId, {
              content: item.toolResult.content,
              status: item.toolResult.status,
              ...(images.length > 0 && { images }),
            })
          }
        }
      }

      const loaded: Message[] = data.messages
        .filter(m => m.role === 'user' || m.role === 'assistant')
        .map((m) => {
          let text = ''
          const toolExecutions: import('../types/chat').ToolExecution[] = []

          const historyFiles: import('../types/chat').UploadedFile[] = []

          if (Array.isArray(m.content)) {
            // Pre-pass: extract original filenames from <uploaded_files> tag
            const uploadedNames: string[] = []
            for (const rawItem of m.content) {
              const t: string = (rawItem as any).text ?? ''
              const match = t.match(/<uploaded_files>([\s\S]*?)<\/uploaded_files>/)
              if (match) {
                for (const line of match[1].split('\n')) {
                  const name = line.replace(/^[-\s]+/, '').trim()
                  if (name && !name.toLowerCase().startsWith('attached')) uploadedNames.push(name)
                }
              }
            }
            let imageIdx = 0

            for (const rawItem of m.content) {
              const item = rawItem as any
              if (item.text) {
                // Strip <uploaded_files>...</uploaded_files> blocks injected by _build_prompt
                text += (item.text as string).replace(/<uploaded_files>[\s\S]*?<\/uploaded_files>/g, '').trim()
              } else if (item.image) {
                const fmt: string = item.image.format || 'jpeg'
                const name = uploadedNames[imageIdx] ?? `image.${fmt}`
                imageIdx++
                historyFiles.push({ name, type: `image/${fmt}` })
              } else if (item.document) {
                // Restore document file badge
                const fmt: string = item.document.format || 'unknown'
                const name: string = item.document.name || 'document'
                const mimeMap: Record<string, string> = {
                  pdf: 'application/pdf',
                  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                  txt: 'text/plain',
                  csv: 'text/csv',
                  html: 'text/html',
                }
                historyFiles.push({
                  name: fmt !== 'unknown' ? `${name}.${fmt}` : name,
                  type: mimeMap[fmt] || 'application/octet-stream',
                })
              } else if (item.toolUse) {
                const result = toolResultMap.get(item.toolUse.toolUseId)
                let resultText = ''
                if (result) {
                  resultText = typeof result.content === 'string'
                    ? result.content
                    : JSON.stringify(result.content)
                }
                toolExecutions.push({
                  id: item.toolUse.toolUseId,
                  toolName: item.toolUse.name,
                  toolInput: typeof item.toolUse.input === 'string'
                    ? item.toolUse.input
                    : JSON.stringify(item.toolUse.input),
                  toolResult: resultText,
                  isComplete: !!result,
                  isExpanded: false,
                  resultStatus: result?.status,
                  images: result?.images,
                  codeSteps: [],
                  codeTodos: [],
                })
              }
            }
          } else if (typeof m.content === 'string') {
            text = m.content
          }

          return {
            ...makeEmptyMessage(m.id || crypto.randomUUID(), m.role === 'user' ? 'user' : 'assistant'),
            text,
            toolExecutions,
            ...(historyFiles.length > 0 && { uploadedFiles: historyFiles }),
          }
        })
        .filter(m => m.text || m.toolExecutions.length > 0)

      // Merge consecutive assistant messages into one bubble
      // (Strands multi-turn: assistant tool-call → user tool-result → assistant → ...)
      const merged: Message[] = []
      for (const msg of loaded) {
        const prev = merged[merged.length - 1]
        if (msg.role === 'assistant' && prev?.role === 'assistant') {
          merged[merged.length - 1] = {
            ...prev,
            text: prev.text && msg.text ? prev.text + '\n\n' + msg.text : prev.text + msg.text,
            toolExecutions: [...prev.toolExecutions, ...msg.toolExecutions],
          }
        } else {
          merged.push(msg)
        }
      }

      console.log('[loadHistory] merged:', merged.length, 'msgs, tools:', merged.map(m =>
        m.toolExecutions.map(t => ({ name: t.toolName, images: t.images?.length ?? 0 }))
      ))

      allHistoryRef.current = merged
      setDisplayCount(PAGE_SIZE)
      setMessages(merged.slice(-PAGE_SIZE))

      // Restore artifacts stored in AgentCore Memory (skip diagram/image — shown inline in chat)
      for (const a of data.artifacts ?? []) {
        if (a.type === 'diagram' || a.type === 'image') continue
        let content: string | import('../types/artifact').ExcalidrawData =
          typeof a.content === 'string'
            ? a.content
            : (a.content as import('../types/artifact').ExcalidrawData)
        if (typeof content === 'string' && content.startsWith('s3://')) {
          content = await resolveS3Content(content)
        }
        addArtifact({
          id: a.id,
          type: a.type as import('../types/artifact').ArtifactType,
          title: a.title,
          content,
          description: a.description,
          timestamp: a.created_at ?? a.timestamp ?? new Date().toISOString(),
          sessionId,
          metadata: a.metadata,
        })
      }
    } catch (err) {
      console.error('[loadHistory] Failed:', err)
    }
  }, [sessionId, clearMessages, setMessages, addArtifact, abortStream])

  const hasMore = allHistoryRef.current.length > displayCount

  const loadMore = useCallback(() => {
    setDisplayCount(prev => {
      const newCount = prev + PAGE_SIZE
      setMessages(allHistoryRef.current.slice(-newCount))
      return newCount
    })
  }, [setMessages])

  const dismissInterrupt = useCallback(
    async (approved: boolean) => {
      if (!pendingInterrupt) return
      const interruptId = pendingInterrupt.interrupts[0]?.id
      setPendingInterrupt(null)

      if (interruptId) {
        setIsSending(true)
        const response = approved ? 'approved' : 'no'
        const interruptMsg = JSON.stringify([{ interruptResponse: { interruptId, response } }])
        await streamSend(interruptMsg, allHistoryRef.current)
      }
    },
    [pendingInterrupt, setPendingInterrupt, streamSend],
  )

  // Only reachable if the backend ever streams an elicitation despite
  // allow_user_federation=false: dismiss locally and let the paused tool time
  // out. There is no callback page here to complete authorization with.
  const dismissOAuth = useCallback(() => {
    setPendingOAuth(null)
  }, [setPendingOAuth])

  return {
    messages,
    agentStatus,
    thinkingMessage,
    isSending,
    networkError,
    pendingOAuth,
    pendingInterrupt,
    isReconnecting,
    reconnectAttempt,
    sendMessage,
    stopStream,
    loadHistory,
    hasMore,
    loadMore,
    clearMessages,
    dismissInterrupt,
    dismissOAuth,
  }
}
