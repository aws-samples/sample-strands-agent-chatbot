import React, { useState, useMemo } from 'react'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Copy, ThumbsUp, ThumbsDown, Check, AudioWaveform, Sparkles } from 'lucide-react'
import { AIIcon } from '@/components/ui/AIIcon'
import { Message } from '@/types/chat'
import { ReasoningState } from '@/types/events'
import { Markdown } from '@/components/ui/Markdown'
import { StreamingText } from './StreamingText'
import { ToolExecutionContainer } from './ToolExecutionContainer'
import { CodeAgentTerminal, isCodeAgentExecution } from './CodeAgentUI'
import { LazyImage } from '@/components/ui/LazyImage'
import { fetchAuthSession } from 'aws-amplify/auth'
import type { ResearchJob } from '@/lib/research-jobs'

// Parse artifact creation message pattern
const parseArtifactMessage = (text: string): { title: string; wordCount: number } | null => {
  // Try with markdown bold first
  let match = text.match(/Document \*\*(.+?)\*\* has been created\.\s*\((\d+) words\)/)
  if (match) {
    return { title: match[1].trim(), wordCount: parseInt(match[2], 10) }
  }
  // Try without markdown bold
  match = text.match(/Document (.+?) has been created\.\s*\((\d+) words\)/)
  if (match) {
    return { title: match[1].trim(), wordCount: parseInt(match[2], 10) }
  }
  return null
}

// Minimal artifact notification - shown instead of text for artifact creation messages
const ArtifactNotification = ({ title, wordCount }: { title: string; wordCount: number }) => {
  const handleClick = () => {
    window.dispatchEvent(new CustomEvent('open-artifact-by-title', { detail: { title } }))
  }

  return (
    <button
      onClick={handleClick}
      className="inline-flex items-center gap-2.5 text-body text-muted-foreground hover:text-foreground transition-colors h-9"
    >
      <Sparkles className="w-4 h-4" />
      <span className="font-medium">{title}</span>
      <span className="text-label opacity-60">· {wordCount.toLocaleString()} words</span>
    </button>
  )
}

interface AssistantTurnProps {
  messages: Message[]
  currentReasoning?: ReasoningState | null
  sessionId?: string
  onOpenResearchArtifact?: (executionId: string) => void
  onOpenWordArtifact?: (filename: string) => void
  onOpenExcelArtifact?: (filename: string) => void
  onOpenPptArtifact?: (filename: string) => void
  onOpenExtractedDataArtifact?: (artifactId: string) => void
  onOpenExcalidrawArtifact?: (artifactId: string) => void
  researchProgress?: {
    stepNumber: number
    content: string
  }
  researchJobs?: ResearchJob[]
  codeProgress?: Array<{
    stepNumber: number
    content: string
  }>
  hideAvatar?: boolean
}

export const AssistantTurn = React.memo<AssistantTurnProps>(({ messages, currentReasoning, sessionId, onOpenResearchArtifact, onOpenWordArtifact, onOpenExcelArtifact, onOpenPptArtifact, onOpenExtractedDataArtifact, onOpenExcalidrawArtifact, researchProgress, researchJobs, codeProgress, hideAvatar = false }) => {
  // Get initial feedback state from first message
  const initialFeedback = messages[0]?.feedback || null

  const [copied, setCopied] = useState(false)
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(initialFeedback)

if (!messages || messages.length === 0) {
    return null
  }

  // Get turn ID from first message for feedback storage
  const turnId = messages[0]?.id

  // Handle copy to clipboard
  const handleCopy = async () => {
    try {
      // Collect all text content from messages
      const allText = messages
        .filter(msg => msg.text)
        .map(msg => msg.text)
        .join('\n\n')

      await navigator.clipboard.writeText(allText)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      console.error('Failed to copy:', err)
    }
  }

  // Handle feedback (thumbs up/down)
  const handleFeedback = async (type: 'up' | 'down') => {
    const newFeedback = feedback === type ? null : type
    setFeedback(newFeedback)

    // Save feedback to metadata
    if (sessionId && turnId) {
      try {
        // Get auth token
        const authHeaders: Record<string, string> = { 'Content-Type': 'application/json' }
        try {
          const session = await fetchAuthSession()
          const token = session.tokens?.accessToken?.toString()
          if (token) {
            authHeaders['Authorization'] = `Bearer ${token}`
          }
        } catch (error) {
          console.log('[AssistantTurn] No auth session available')
        }

        await fetch('/api/session/update-metadata', {
          method: 'POST',
          headers: authHeaders,
          body: JSON.stringify({
            sessionId,
            messageId: turnId,
            metadata: {
              feedback: newFeedback
            }
          })
        })
      } catch (err) {
        console.error('Failed to save feedback:', err)
      }
    }
  }

  // Sort messages by timestamp to maintain chronological order
  // All messages have timestamp set on creation, so no fallback needed
  const sortedMessages = useMemo(() => {
    return [...messages].sort((a, b) => {
      const timeA = new Date(a.timestamp).getTime()
      const timeB = new Date(b.timestamp).getTime()
      return timeA - timeB
    })
  }, [messages])

  // Group consecutive text messages together while preserving tool message positions
  const groupedContent = useMemo(() => {
    const grouped: Array<{
      type: 'text' | 'tool' | 'artifact'
      content: string | Message
      images?: any[]
      key: string
      toolUseId?: string
      isStreaming?: boolean
      artifact?: { title: string; wordCount: number }
    }> = []

    let currentTextGroup = ''
    let currentTextImages: any[] = []
    let textGroupStartId: string | number = 0
    let currentToolUseId: string | undefined = undefined
    let textGroupCounter = 0 // Counter for unique keys
    let currentIsStreaming = false // Track if any message in group is streaming

    const flushTextGroup = () => {
      if (currentTextGroup.trim()) {
        grouped.push({
          type: 'text',
          content: currentTextGroup,
          images: currentTextImages,
          key: `text-group-${textGroupCounter}-${textGroupStartId}`, // Use counter + id for uniqueness
          toolUseId: currentToolUseId,
          isStreaming: currentIsStreaming
        })
        currentTextGroup = ''
        currentTextImages = []
        currentToolUseId = undefined
        currentIsStreaming = false
        textGroupCounter++ // Increment counter
      }
    }

    sortedMessages.forEach((message) => {
      // Check if message has tool executions
      const hasToolExecutions = message.toolExecutions && message.toolExecutions.length > 0

      if (hasToolExecutions) {
        // Message has tool executions - render text first, then tools

        // Add text if present
        if (message.text) {
          if (!currentTextGroup) {
            textGroupStartId = typeof message.id === 'number' ? message.id : 0
          }
          currentTextGroup += message.text
          if (message.images && message.images.length > 0) {
            currentTextImages.push(...message.images)
          }
          // Track streaming state
          if (message.isStreaming) {
            currentIsStreaming = true
          }
        }

        // Flush text group before tool container
        flushTextGroup()

        // Add tool execution container
        grouped.push({
          type: 'tool',
          content: message,
          key: `tool-${message.id}`
        })
      } else if (message.artifactReference) {
        // Artifact reference from real-time update
        flushTextGroup()
        grouped.push({
          type: 'artifact',
          content: '',
          key: `artifact-${message.id}`,
          artifact: {
            title: message.artifactReference.title,
            wordCount: message.artifactReference.wordCount || 0
          }
        })
      } else if (message.text) {
        // Text-only message - accumulate
        if (!currentTextGroup) {
          textGroupStartId = typeof message.id === 'number' ? message.id : 0
        }
        currentTextGroup += message.text
        if (message.images && message.images.length > 0) {
          currentTextImages.push(...message.images)
        }
        // Track toolUseId for this text message
        if (message.toolUseId && !currentToolUseId) {
          currentToolUseId = message.toolUseId
        }
        // Track streaming state
        if (message.isStreaming) {
          currentIsStreaming = true
        }
      }
    })

    // Flush any remaining text
    flushTextGroup()

    return grouped
  }, [sortedMessages])

  // Find latency metrics and token usage from the messages
  const latencyMetrics = sortedMessages.find(msg => msg.latencyMetrics)?.latencyMetrics
  const tokenUsage = sortedMessages.find(msg => msg.tokenUsage)?.tokenUsage

  return (
    <div className="flex justify-start mb-8 group">
      <div className={`flex items-start max-w-4xl w-full min-w-0 ${hideAvatar ? '' : 'space-x-4'}`}>
        {/* Single Avatar for the entire turn - hidden when part of Swarm response */}
        {!hideAvatar && (
          messages.some(m => m.isVoiceMessage) ? (
            <div className="h-8 w-8 flex-shrink-0 mt-2 flex items-center justify-center rounded-md bg-muted text-primary">
              <AudioWaveform className="h-4 w-4" />
            </div>
          ) : (
            <AIIcon size={32} isAnimating={messages.some(m => m.isStreaming)} className="mt-2" />
          )
        )}

        {/* Turn Content - add left margin when avatar is hidden to align with SwarmProgress content */}
        <div className={`flex-1 space-y-4 pt-1 min-w-0 ${hideAvatar ? 'ml-12' : ''}`}>
          {/* Render messages in chronological order — merge consecutive tool items */}
          {groupedContent.map((item, index) => {
            if (item.type === 'tool') {
              // Skip if this tool item was already merged into a previous consecutive tool group
              if (index > 0 && groupedContent[index - 1]?.type === 'tool') return null

              // Collect all consecutive tool items starting from this one
              const mergedToolExecutions: import('@/types/chat').ToolExecution[] = []
              let j = index
              while (j < groupedContent.length && groupedContent[j].type === 'tool') {
                const msg = groupedContent[j].content as Message
                mergedToolExecutions.push(...(msg.toolExecutions || []))
                j++
              }

              return (
                <div key={item.key} className="animate-fade-in space-y-4">
                  {mergedToolExecutions.length > 0 && (
                    <ToolExecutionContainer
                      toolExecutions={mergedToolExecutions}
                      sessionId={sessionId}
                      onOpenResearchArtifact={onOpenResearchArtifact}
                      onOpenWordArtifact={onOpenWordArtifact}
                      onOpenExcelArtifact={onOpenExcelArtifact}
                      onOpenPptArtifact={onOpenPptArtifact}
                      onOpenExtractedDataArtifact={onOpenExtractedDataArtifact}
                      onOpenExcalidrawArtifact={onOpenExcalidrawArtifact}
                      researchJobs={researchJobs}
                    />
                  )}
                </div>
              )
            }

            // Handle artifact type (real-time updates via artifactReference)
            if (item.type === 'artifact' && item.artifact) {
              return (
                <div key={item.key} className="animate-fade-in">
                  <ArtifactNotification title={item.artifact.title} wordCount={item.artifact.wordCount} />
                </div>
              )
            }

            // Check for artifact creation pattern (history load)
            const textContent = item.content as string
            const artifact = parseArtifactMessage(textContent)

            // If this is an artifact message, render as notification
            if (artifact) {
              return (
                <div key={item.key} className="animate-fade-in">
                  <ArtifactNotification title={artifact.title} wordCount={artifact.wordCount} />
                </div>
              )
            }

            return (
              <div key={item.key} className="animate-fade-in">
                <div className="chat-chart-content w-full overflow-hidden">
                  {/* Use StreamingText for smooth typing animation during streaming */}
                  <StreamingText
                    text={textContent}
                    isStreaming={item.isStreaming || false}
                    sessionId={sessionId}
                    toolUseId={item.toolUseId}
                  />

                  {/* Generated Images for this text group */}
                  {item.images && item.images.length > 0 && (
                    <div className="mt-4 space-y-3">
                      {item.images.map((image, idx) => {
                        // Type guard for URL-based images
                        const isUrlImage = 'type' in image && image.type === 'url';
                        const imageSrc = isUrlImage
                          ? (image.url || image.thumbnail || '')
                          : 'data' in image
                          ? `data:image/${image.format};base64,${image.data}`
                          : '';
                        const imageFormat = isUrlImage
                          ? 'WEB'
                          : 'format' in image
                          ? (image.format || 'IMG').toUpperCase()
                          : 'IMG';

                        // Skip rendering if no valid image source
                        if (!imageSrc) return null;

                        return (
                          <div key={idx} className="relative group">
                            <LazyImage
                              src={imageSrc}
                              alt={`Generated image ${idx + 1}`}
                              className="max-w-full h-auto rounded-xl border border-border shadow-xs"
                              style={{ maxHeight: '400px' }}
                            />
                            <div className="absolute top-3 right-3 opacity-0 group-hover:opacity-100 transition-opacity">
                              <Badge variant="secondary" className="text-caption bg-black/70 text-white border-0">
                                {imageFormat}
                              </Badge>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            )
          })}

          {/* Code agent real-time progress — live terminal for active code agent */}
          {codeProgress && codeProgress.length > 0 && messages.some(m =>
            m.toolExecutions?.some(t => isCodeAgentExecution(t) && !t.isComplete)
          ) && (
            <CodeAgentTerminal steps={codeProgress} />
          )}

          {/* Metrics - Minimal text on hover (hidden on mobile) */}
          {((latencyMetrics && (latencyMetrics.timeToFirstToken || latencyMetrics.endToEndLatency)) || tokenUsage) && (
            <div className="hidden md:flex justify-end opacity-0 group-hover:opacity-100 transition-opacity duration-200 -mt-1">
              <span className="text-[11px] text-muted-foreground/70">
                {[
                  latencyMetrics?.timeToFirstToken && `TTFT ${latencyMetrics.timeToFirstToken}ms`,
                  latencyMetrics?.endToEndLatency && `E2E ${(latencyMetrics.endToEndLatency / 1000).toFixed(1)}s`,
                  tokenUsage && `${(tokenUsage.inputTokens / 1000).toFixed(1)}k in · ${tokenUsage.outputTokens} out${
                    (tokenUsage.cacheReadInputTokens ?? 0) > 0 || (tokenUsage.cacheWriteInputTokens ?? 0) > 0
                      ? ` (${[
                          (tokenUsage.cacheReadInputTokens ?? 0) > 0 && `${tokenUsage.cacheReadInputTokens!.toLocaleString()} hit`,
                          (tokenUsage.cacheWriteInputTokens ?? 0) > 0 && `${tokenUsage.cacheWriteInputTokens!.toLocaleString()} write`,
                        ].filter(Boolean).join(', ')})`
                      : ''
                  }`,
                ].filter(Boolean).join(' · ')}
              </span>
            </div>
          )}

          {/* Action Buttons - Shows on hover at bottom */}
          <div className="flex gap-2 mt-2 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
            <Button
              variant="ghost"
              size="sm"
              onClick={handleCopy}
              className="h-8 px-3 text-muted-foreground hover:text-foreground hover:bg-muted"
            >
              {copied ? (
                <Check className="h-3.5 w-3.5" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </Button>

            <Button
              variant="ghost"
              size="sm"
              onClick={() => handleFeedback('up')}
              className={`h-8 px-3 ${
                feedback === 'up'
                  ? 'text-green-600 bg-green-500/10 hover:bg-green-500/20 dark:text-green-400'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              }`}
            >
              <ThumbsUp className="h-3.5 w-3.5" />
            </Button>

            <Button
              variant="ghost"
              size="sm"
              onClick={() => handleFeedback('down')}
              className={`h-8 px-3 ${
                feedback === 'down'
                  ? 'text-destructive bg-destructive/10 hover:bg-destructive/20'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              }`}
            >
              <ThumbsDown className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}, (prevProps, nextProps) => {
  // Only re-render if messages or reasoning actually changed
  const messagesEqual = prevProps.messages.length === nextProps.messages.length &&
    prevProps.messages.every((msg, idx) => {
      const nextMsg = nextProps.messages[idx]
      if (!nextMsg) return false

      // Compare basic properties
      if (msg.id !== nextMsg.id || msg.text !== nextMsg.text) return false

      // Compare latencyMetrics (important for showing metrics after streaming)
      const latencyChanged =
        msg.latencyMetrics?.timeToFirstToken !== nextMsg.latencyMetrics?.timeToFirstToken ||
        msg.latencyMetrics?.endToEndLatency !== nextMsg.latencyMetrics?.endToEndLatency

      // Compare tokenUsage (important for showing token counts after streaming)
      const tokenUsageChanged =
        msg.tokenUsage?.inputTokens !== nextMsg.tokenUsage?.inputTokens ||
        msg.tokenUsage?.outputTokens !== nextMsg.tokenUsage?.outputTokens

      // If metrics changed, we need to re-render
      if (latencyChanged || tokenUsageChanged) return false

      // Compare toolExecutions (critical for preventing flickering during tool updates)
      const prevToolExecs = msg.toolExecutions || []
      const nextToolExecs = nextMsg.toolExecutions || []

      if (prevToolExecs.length !== nextToolExecs.length) return false

      const toolExecutionsChanged = prevToolExecs.some((tool, toolIdx) => {
        const nextTool = nextToolExecs[toolIdx]
        if (!nextTool) return true

        // Compare critical tool execution fields
        if (tool.id !== nextTool.id) return true
        if (tool.isComplete !== nextTool.isComplete) return true
        if (tool.toolResult !== nextTool.toolResult) return true
        if (tool.streamingResponse !== nextTool.streamingResponse) return true

        // Compare toolInput to detect parameter updates
        // PERFORMANCE: Use reference equality check first
        if (tool.toolInput === nextTool.toolInput) return false

        // Deep comparison only if references differ
        const prevInput = JSON.stringify(tool.toolInput || {})
        const nextInput = JSON.stringify(nextTool.toolInput || {})
        if (prevInput !== nextInput) return true

        return false
      })

      if (toolExecutionsChanged) return false

      return true
    })

  const reasoningEqual = prevProps.currentReasoning?.text === nextProps.currentReasoning?.text
  const callbackEqual = prevProps.onOpenResearchArtifact === nextProps.onOpenResearchArtifact &&
    prevProps.onOpenWordArtifact === nextProps.onOpenWordArtifact &&
    prevProps.onOpenExcelArtifact === nextProps.onOpenExcelArtifact &&
    prevProps.onOpenPptArtifact === nextProps.onOpenPptArtifact &&
    prevProps.onOpenExtractedDataArtifact === nextProps.onOpenExtractedDataArtifact &&
    prevProps.onOpenExcalidrawArtifact === nextProps.onOpenExcalidrawArtifact

  // Compare researchProgress for real-time status updates
  const researchProgressEqual = prevProps.researchProgress?.stepNumber === nextProps.researchProgress?.stepNumber &&
    prevProps.researchProgress?.content === nextProps.researchProgress?.content

  // Compare codeProgress for real-time code agent status
  const codeProgressEqual = (prevProps.codeProgress?.length ?? 0) === (nextProps.codeProgress?.length ?? 0)

  const hideAvatarEqual = prevProps.hideAvatar === nextProps.hideAvatar
  return messagesEqual && reasoningEqual && prevProps.sessionId === nextProps.sessionId && callbackEqual && researchProgressEqual && codeProgressEqual && hideAvatarEqual
})
