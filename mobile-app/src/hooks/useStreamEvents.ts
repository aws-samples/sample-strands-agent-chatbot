import { useCallback, useRef, useState } from 'react'
import { v4 as uuidv4 } from 'uuid'
import type { AGUIEvent, AnyCustomEvent, ToolCallResultContent } from '../types/events'
import {
  type Message,
  type ToolExecution,
  type AgentStatus,
  type PendingOAuth,
  type PendingInterrupt,
  makeEmptyMessage,
} from '../types/chat'
import { TEXT_BUFFER_FLUSH_MS } from '../lib/constants'
import type { ArtifactSignal, CompletedToolSnapshot } from '../types/artifact'

/** Internal / wrapper tool names that should not be shown verbatim to the user. */
const INTERNAL_TOOLS = new Set([
  'skill_dispatcher', 'tool_executor',
])

const WORKING_PHRASES = [
  'Working on it…',
  'On it…',
  'Hang tight…',
  'Getting things ready…',
  'Let me work on that…',
]

/** Pick a random engaging phrase for generic tool activity. */
function pickWorkingPhrase(): string {
  const buf = new Uint8Array(1)
  crypto.getRandomValues(buf)
  return WORKING_PHRASES[buf[0] % WORKING_PHRASES.length]
}

/** Return a user-friendly thinking message for a tool name. */
function friendlyToolMessage(toolName: string): string {
  if (!toolName || INTERNAL_TOOLS.has(toolName)) return pickWorkingPhrase()
  // snake_case → Title Case
  const pretty = toolName.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
  return `Using ${pretty}…`
}

/**
 * Manages all AG-UI event → React state transformations.
 * Returns messages, status state, and a stable handleEvent dispatcher.
 */
interface UseStreamEventsOpts {
  /** Called when an execution_meta event arrives with the executionId. */
  onExecutionMeta?: (executionId: string) => void
  /** Called when artifact-related signals are detected (synchronous dispatch; async handling lives in useChat). */
  onArtifactSignal?: (signal: ArtifactSignal) => void
}

export function useStreamEvents(opts?: UseStreamEventsOpts) {
  // Keep a ref so handleEvent always calls the latest version without dep churn
  const onExecutionMetaRef = useRef(opts?.onExecutionMeta)
  onExecutionMetaRef.current = opts?.onExecutionMeta
  const onArtifactSignalRef = useRef(opts?.onArtifactSignal)
  onArtifactSignalRef.current = opts?.onArtifactSignal

  const [messages, setMessages] = useState<Message[]>([])
  const [agentStatus, setAgentStatus] = useState<AgentStatus>('idle')
  const [thinkingMessage, setThinkingMessage] = useState('Processing…')
  const [pendingOAuth, setPendingOAuth] = useState<PendingOAuth | null>(null)
  const [pendingInterrupt, setPendingInterrupt] = useState<PendingInterrupt | null>(null)

  // Deduplication set for SSE reconnect replay (keyed by "_eventId:type")
  const seenEventIdsRef = useRef(new Set<string>())

  // Refs for streaming state that must not trigger re-renders
  const textBufferRef = useRef('')
  const textTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const currentMsgIdRef = useRef<string | null>(null)
  const toolArgAccRef = useRef<Record<string, string>>({})
  // ID of the last assistant message (for attaching complete_metadata)
  const lastAssistantMsgIdRef = useRef<string | null>(null)
  // Completed tool snapshots accumulated during a run, cleared on RUN_STARTED/RUN_FINISHED
  const completedToolsRef = useRef<CompletedToolSnapshot[]>([])

  // ─── Text buffer helpers ──────────────────────────────────────────────────

  const startTextBuffer = useCallback((messageId: string) => {
    currentMsgIdRef.current = messageId
    if (textTimerRef.current) clearInterval(textTimerRef.current)
    textTimerRef.current = setInterval(() => {
      const delta = textBufferRef.current
      if (!delta) return
      textBufferRef.current = ''
      setMessages(prev =>
        prev.map(m => (m.id === messageId ? { ...m, text: m.text + delta } : m)),
      )
    }, TEXT_BUFFER_FLUSH_MS)
  }, [])

  const stopTextBuffer = useCallback(() => {
    if (textTimerRef.current) {
      clearInterval(textTimerRef.current)
      textTimerRef.current = null
    }
    const delta = textBufferRef.current
    const msgId = currentMsgIdRef.current
    textBufferRef.current = ''
    currentMsgIdRef.current = null
    if (delta && msgId) {
      setMessages(prev => prev.map(m => (m.id === msgId ? { ...m, text: m.text + delta } : m)))
    }
  }, [])

  // ─── Tool execution helpers ───────────────────────────────────────────────

  const updateTool = useCallback(
    (toolId: string, updater: (t: ToolExecution) => ToolExecution) => {
      setMessages(prev =>
        prev.map(m => ({
          ...m,
          toolExecutions: m.toolExecutions.map(t => (t.id === toolId ? updater(t) : t)),
        })),
      )
    },
    [],
  )

  // Append a tool to the latest assistant message (or create one if none exists yet)
  const appendTool = useCallback((tool: ToolExecution) => {
    setMessages(prev => {
      const lastIdx = prev.length - 1
      if (lastIdx >= 0 && prev[lastIdx].role === 'assistant') {
        // Skip if tool already exists (SSE reconnect replay dedup)
        if (prev[lastIdx].toolExecutions.some(t => t.id === tool.id)) return prev
        return prev.map((m, i) =>
          i === lastIdx ? { ...m, toolExecutions: [...m.toolExecutions, tool] } : m,
        )
      }
      // No assistant message yet — create a carrier message
      const carrier = makeEmptyMessage(uuidv4(), 'assistant')
      return [...prev, { ...carrier, toolExecutions: [tool] }]
    })
  }, [])

  // ─── Master event dispatcher ──────────────────────────────────────────────

  const handleEvent = useCallback(
    (event: AGUIEvent) => {
      // Deduplicate replayed events during SSE reconnect.
      // The BFF stamps replayed events with a numeric _eventId; live events won't have it.
      const eventId = (event as unknown as Record<string, unknown>)._eventId as number | undefined
      if (eventId && eventId > 0) {
        const key = `${eventId}:${event.type}`
        if (seenEventIdsRef.current.has(key)) return
        seenEventIdsRef.current.add(key)
      }

      if (__DEV__ && event.type !== 'TEXT_MESSAGE_CONTENT' && event.type !== 'TOOL_CALL_ARGS') {
        console.log('[AGUI]', event.type, 'type' in event && event.type === 'CUSTOM' ? (event as AnyCustomEvent).name : '')
      }

      switch (event.type) {
        // ── Standard AG-UI ────────────────────────────────────────────────

        case 'RUN_STARTED':
          completedToolsRef.current = []
          setAgentStatus('thinking')
          setThinkingMessage('Processing…')
          break

        case 'TEXT_MESSAGE_START': {
          const messageId = event.messageId
          lastAssistantMsgIdRef.current = messageId
          startTextBuffer(messageId)
          setAgentStatus('responding')
          setMessages(prev => {
            const lastIdx = prev.length - 1
            const last = prev[lastIdx]
            // Always reuse the last assistant message to keep tool calls + text
            // in a single bubble (matches how loadHistory merges them).
            if (last?.role === 'assistant') {
              return prev.map((m, i) =>
                i === lastIdx
                  ? {
                      ...m,
                      id: messageId,
                      isStreaming: true,
                      // Add paragraph separator if there's already text
                      text: m.text ? m.text + '\n\n' : m.text,
                    }
                  : m,
              )
            }
            return [...prev, { ...makeEmptyMessage(messageId, 'assistant'), isStreaming: true }]
          })
          break
        }

        case 'TEXT_MESSAGE_CONTENT':
          textBufferRef.current += event.delta
          break

        case 'TEXT_MESSAGE_END':
          stopTextBuffer()
          setMessages(prev =>
            prev.map(m =>
              m.id === event.messageId ? { ...m, isStreaming: false } : m,
            ),
          )
          break

        case 'TOOL_CALL_START': {
          const tool: ToolExecution = {
            id: event.toolCallId,
            toolName: event.toolCallName,
            toolInput: '',
            isComplete: false,
            isExpanded: false,
            codeSteps: [],
            codeTodos: [],
          }
          appendTool(tool)
          toolArgAccRef.current[event.toolCallId] = ''
          // Store tool name for artifact detection at TOOL_CALL_RESULT time
          toolArgAccRef.current[`__name__${event.toolCallId}`] = event.toolCallName
          setAgentStatus('thinking')
          setThinkingMessage(friendlyToolMessage(event.toolCallName))
          break
        }

        case 'TOOL_CALL_ARGS': {
          toolArgAccRef.current[event.toolCallId] =
            (toolArgAccRef.current[event.toolCallId] ?? '') + event.delta
          const accArgs = toolArgAccRef.current[event.toolCallId] ?? ''
          updateTool(event.toolCallId, t => ({
            ...t,
            toolInput: accArgs,
          }))
          // Once args are parseable, try to show a friendlier inner tool name
          const rawName = toolArgAccRef.current[`__name__${event.toolCallId}`] ?? ''
          if (INTERNAL_TOOLS.has(rawName)) {
            try {
              const p = JSON.parse(accArgs) as Record<string, unknown>
              const inner = (p.skill_name ?? p.skill ?? p.tool_name) as string | undefined
              if (inner) setThinkingMessage(friendlyToolMessage(inner))
            } catch { /* args still incomplete */ }
          }
          break
        }

        case 'TOOL_CALL_END':
          // Args finalized — nothing extra to do; spinner continues until RESULT
          break

        case 'TOOL_CALL_RESULT': {
          let parsed: ToolCallResultContent = { result: '' }
          try {
            parsed = JSON.parse(event.content) as ToolCallResultContent
          } catch {
            parsed = { result: event.content }
          }
          console.log('[AGUI] TOOL_CALL_RESULT images:', parsed.images?.length ?? 0, 'metadata:', Object.keys(parsed.metadata ?? {}))
          updateTool(event.toolCallId, t => ({
            ...t,
            toolResult: parsed.result,
            images: parsed.images,
            metadata: parsed.metadata,
            resultStatus: parsed.status,
            isComplete: true,
          }))

          // Snapshot the completed tool for artifact detection at RUN_FINISHED
          const toolName = (() => {
            // Retrieve toolName from accumulated args accumulator key; fall back to empty
            // The tool name is available on the TOOL_CALL_START event which has already
            // been processed — read it from the current messages state via messages ref isn't
            // practical here; instead we store it in toolArgAccRef alongside args.
            // We use a side-channel: store name in toolArgAccRef with a sentinel key.
            return (toolArgAccRef.current[`__name__${event.toolCallId}`] as string | undefined) ?? ''
          })()
          const rawArgs = toolArgAccRef.current[event.toolCallId] ?? ''
          let toolInputParsed: unknown = rawArgs
          try { toolInputParsed = JSON.parse(rawArgs) } catch { /* leave as string */ }

          completedToolsRef.current.push({
            toolCallId: event.toolCallId,
            toolName,
            toolInputParsed,
            toolResult: parsed.result,
            metadata: (parsed.metadata as Record<string, unknown>) ?? {},
          })

          // Immediate signal for excalidraw tool
          if (toolName === 'create_excalidraw_diagram') {
            let excalidrawData: Record<string, unknown> = {}
            try { excalidrawData = JSON.parse(parsed.result) as Record<string, unknown> } catch { /* use empty */ }
            onArtifactSignalRef.current?.({
              kind: 'excalidraw',
              data: excalidrawData,
              toolCallId: event.toolCallId,
            })
          }

          // Immediate signal for browser_extract with artifactId
          if (
            toolName === 'browser_extract' &&
            parsed.metadata &&
            typeof (parsed.metadata as Record<string, unknown>).artifactId === 'string'
          ) {
            onArtifactSignalRef.current?.({
              kind: 'browser_extract',
              artifactId: (parsed.metadata as Record<string, unknown>).artifactId as string,
              toolOutput: parsed.result,
              metadata: parsed.metadata as Record<string, unknown>,
            })
          }

          delete toolArgAccRef.current[event.toolCallId]
          delete toolArgAccRef.current[`__name__${event.toolCallId}`]
          break
        }

        case 'REASONING_START':
        case 'REASONING_MESSAGE_START':
          setAgentStatus('thinking')
          break

        case 'REASONING_MESSAGE_CONTENT':
          setMessages(prev => {
            const lastIndex = prev.length - 1
            const last = prev[lastIndex]
            if (last?.role === 'assistant') {
              return prev.map((message, index) =>
                index === lastIndex
                  ? {
                      ...message,
                      reasoningText: message.reasoningText + event.delta,
                    }
                  : message,
              )
            }
            const carrier = makeEmptyMessage(uuidv4(), 'assistant')
            return [
              ...prev,
              { ...carrier, reasoningText: event.delta },
            ]
          })
          break

        case 'REASONING_MESSAGE_END':
        case 'REASONING_END':
          break

        case 'RUN_FINISHED': {
          if (event.outcome?.type === 'interrupt') {
            const interrupts = (event.outcome.interrupts ?? []).map(interrupt => ({
              id: interrupt.id,
              name: interrupt.metadata?.name ?? interrupt.message ?? 'interrupt',
              reason: interrupt.metadata?.reason,
            }))
            if (interrupts.length > 0) {
              setPendingInterrupt({ interrupts })
            }
          }
          stopTextBuffer()
          // Fire run_finished signal with all completed tools
          if (completedToolsRef.current.length > 0) {
            onArtifactSignalRef.current?.({
              kind: 'run_finished',
              completedTools: [...completedToolsRef.current],
            })
          }
          completedToolsRef.current = []
          setAgentStatus('idle')
          break
        }

        case 'RUN_ERROR': {
          stopTextBuffer()
          const errMsg = makeEmptyMessage(uuidv4(), 'error')
          setMessages(prev => [...prev, { ...errMsg, text: event.message }])
          setAgentStatus('idle')
          break
        }

        // ── CUSTOM events ─────────────────────────────────────────────────

        case 'CUSTOM': {
          const custom = event as AnyCustomEvent
          switch (custom.name) {
            case 'thinking':
              setThinkingMessage(custom.value.message)
              break

            case 'reasoning':
              setMessages(prev => {
                if (prev.length === 0) return prev
                const last = prev[prev.length - 1]
                if (last.role !== 'assistant') return prev
                return prev.map((m, i) =>
                  i === prev.length - 1
                    ? { ...m, reasoningText: m.reasoningText + custom.value.text }
                    : m,
                )
              })
              break

            case 'stream_stopped':
              stopTextBuffer()
              setAgentStatus('idle')
              break

            case 'complete_metadata': {
              const targetId = lastAssistantMsgIdRef.current
              if (targetId) {
                setMessages(prev =>
                  prev.map(m =>
                    m.id === targetId
                      ? {
                          ...m,
                          images: custom.value.images ?? m.images,
                          tokenUsage: custom.value.usage,
                        }
                      : m,
                  ),
                )
              }
              break
            }

            case 'interrupt':
              setPendingInterrupt({ interrupts: custom.value.interrupts })
              break

            case 'warning': {
              const warnMsg = makeEmptyMessage(uuidv4(), 'warning')
              setMessages(prev => [...prev, { ...warnMsg, text: custom.value.message }])
              break
            }

            case 'browser_progress':
              setMessages(prev => {
                if (prev.length === 0) return prev
                const last = prev[prev.length - 1]
                if (last.role !== 'assistant') return prev
                return prev.map((m, i) =>
                  i === prev.length - 1
                    ? {
                        ...m,
                        browserProgress: [
                          ...m.browserProgress,
                          { stepNumber: custom.value.stepNumber, content: custom.value.content },
                        ],
                      }
                    : m,
                )
              })
              break

            case 'research_progress':
              setMessages(prev => {
                if (prev.length === 0) return prev
                const last = prev[prev.length - 1]
                if (last.role !== 'assistant') return prev
                return prev.map((m, i) =>
                  i === prev.length - 1
                    ? {
                        ...m,
                        researchProgress: [
                          ...m.researchProgress,
                          { stepNumber: custom.value.stepNumber, content: custom.value.content },
                        ],
                      }
                    : m,
                )
              })
              break

            case 'code_step': {
              // Attach to the last incomplete tool execution
              const step = { stepNumber: custom.value.stepNumber, content: custom.value.content }
              setMessages(prev =>
                prev.map(m => ({
                  ...m,
                  toolExecutions: m.toolExecutions.map((t, i) => {
                    const isLast = i === m.toolExecutions.length - 1
                    return isLast && !t.isComplete ? { ...t, codeSteps: [...t.codeSteps, step] } : t
                  }),
                })),
              )
              break
            }

            case 'code_todo_update':
              setMessages(prev =>
                prev.map(m => ({
                  ...m,
                  toolExecutions: m.toolExecutions.map((t, i) => {
                    const isLast = i === m.toolExecutions.length - 1
                    return isLast && !t.isComplete
                      ? { ...t, codeTodos: custom.value.todos }
                      : t
                  }),
                })),
              )
              break

            case 'code_result_meta':
              setMessages(prev =>
                prev.map(m => ({
                  ...m,
                  toolExecutions: m.toolExecutions.map((t, i) => {
                    const isLast = i === m.toolExecutions.length - 1
                    return isLast
                      ? { ...t, codeResultMeta: custom.value }
                      : t
                  }),
                })),
              )
              break

            case 'oauth_elicitation':
              setPendingOAuth({
                authUrl: custom.value.authUrl,
                message: custom.value.message,
                elicitationId: custom.value.elicitationId,
              })
              break

            case 'swarm_node_start':
              setMessages(prev => {
                if (prev.length === 0) return prev
                const last = prev[prev.length - 1]
                if (last.role !== 'assistant') return prev
                return prev.map((m, i) =>
                  i === prev.length - 1
                    ? {
                        ...m,
                        swarmAgentSteps: [
                          ...m.swarmAgentSteps,
                          {
                            nodeId: custom.value.node_id,
                            description: custom.value.node_description,
                            status: 'running' as const,
                          },
                        ],
                      }
                    : m,
                )
              })
              break

            case 'swarm_node_stop':
              setMessages(prev =>
                prev.map(m => ({
                  ...m,
                  swarmAgentSteps: m.swarmAgentSteps.map(s =>
                    s.nodeId === custom.value.node_id
                      ? {
                          ...s,
                          status: custom.value.status === 'completed' ? 'completed' : 'failed',
                        }
                      : s,
                  ),
                })),
              )
              break

            case 'swarm_handoff':
              setMessages(prev =>
                prev.map(m => ({
                  ...m,
                  swarmAgentSteps: m.swarmAgentSteps.map(s =>
                    s.nodeId === custom.value.from_node
                      ? { ...s, handoffTo: custom.value.to_node }
                      : s,
                  ),
                })),
              )
              break

            case 'swarm_complete':
              setMessages(prev =>
                prev.map((m, i) =>
                  i === prev.length - 1 ? { ...m, swarmCompleted: true } : m,
                ),
              )
              break

            case 'metadata':
              // Stored in state silently; not rendered in prototype
              break

            case 'execution_meta':
              onExecutionMetaRef.current?.(custom.value.executionId)
              break

            case 'code_agent_started':
              setAgentStatus('thinking')
              setThinkingMessage('Code agent running…')
              break

            case 'code_agent_heartbeat':
              setThinkingMessage(`Code agent running… (${custom.value.elapsed_seconds}s)`)
              break

            default:
              break
          }
          break
        }

        default:
          break
      }
    },
    [appendTool, startTextBuffer, stopTextBuffer, updateTool],
  )

  const resetStreamState = useCallback(() => {
    stopTextBuffer()
    toolArgAccRef.current = {}
    lastAssistantMsgIdRef.current = null
    seenEventIdsRef.current.clear()
    setAgentStatus('idle')
    setThinkingMessage('Processing…')
  }, [stopTextBuffer])

  const clearMessages = useCallback(() => {
    resetStreamState()
    setMessages([])
    setPendingOAuth(null)
    setPendingInterrupt(null)
  }, [resetStreamState])

  return {
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
  }
}
