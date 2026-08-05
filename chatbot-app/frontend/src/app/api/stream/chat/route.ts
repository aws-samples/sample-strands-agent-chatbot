/**
 * Chat streaming endpoint (BFF)
 * Invokes AgentCore Runtime and streams responses
 */
import { NextRequest } from 'next/server'
import { invokeAgentCoreRuntime } from '@/lib/agentcore-runtime-client'
import { extractUserFromRequest, getSessionId, ensureSessionExists } from '@/lib/auth-utils'
import { createDefaultHookManager } from '@/lib/chat-hooks'
import sharp from 'sharp'

// Maximum image size in bytes — must stay well under AgentCore Memory's
// CreateEvent payload limit. Raw bytes become ~33% larger in base64.
const MAX_IMAGE_SIZE = 1.5 * 1024 * 1024

/**
 * Resize image if it exceeds max size
 * Progressively reduces quality and resolution until under limit
 */
async function resizeImageIfNeeded(
  buffer: Buffer,
  contentType: string,
  filename: string
): Promise<{ buffer: Buffer; resized: boolean }> {
  // Only process images
  if (!contentType.startsWith('image/')) {
    return { buffer, resized: false }
  }

  // Skip if already under limit
  if (buffer.length <= MAX_IMAGE_SIZE) {
    return { buffer, resized: false }
  }

  const safeName = filename.replace(/[^\w.\-]/g, '_').slice(0, 100)
  console.log(`[BFF] Image ${safeName} is ${(buffer.length / 1024 / 1024).toFixed(2)}MB, resizing...`)

  // Determine output format (convert to jpeg for better compression, keep png for transparency)
  const isPng = contentType === 'image/png'
  const isGif = contentType === 'image/gif'

  // Don't process GIFs (animated)
  if (isGif) {
    console.log(`[BFF] Skipping GIF resize (may be animated)`)
    return { buffer, resized: false }
  }

  let result = buffer
  let quality = 85
  const maxDimension = 2048

  try {
    // First pass: resize to max dimension and initial quality
    let sharpInstance = sharp(buffer)
      .resize(maxDimension, maxDimension, {
        fit: 'inside',
        withoutEnlargement: true
      })

    if (isPng) {
      result = await sharpInstance.png({ quality, compressionLevel: 9 }).toBuffer()
    } else {
      result = await sharpInstance.jpeg({ quality }).toBuffer()
    }

    // Progressive quality reduction if still too large
    while (result.length > MAX_IMAGE_SIZE && quality > 30) {
      quality -= 10
      sharpInstance = sharp(buffer)
        .resize(maxDimension, maxDimension, {
          fit: 'inside',
          withoutEnlargement: true
        })

      if (isPng) {
        // For PNG, also reduce colors if quality is low
        result = await sharpInstance.png({ quality, compressionLevel: 9 }).toBuffer()
      } else {
        result = await sharpInstance.jpeg({ quality }).toBuffer()
      }
    }

    // If still too large, reduce dimensions further
    if (result.length > MAX_IMAGE_SIZE) {
      const reducedDimension = 1024
      sharpInstance = sharp(buffer)
        .resize(reducedDimension, reducedDimension, {
          fit: 'inside',
          withoutEnlargement: true
        })

      if (isPng) {
        result = await sharpInstance.png({ quality: 60, compressionLevel: 9 }).toBuffer()
      } else {
        result = await sharpInstance.jpeg({ quality: 60 }).toBuffer()
      }
    }

    console.log(`[BFF] Resized ${safeName}: ${(buffer.length / 1024 / 1024).toFixed(2)}MB -> ${(result.length / 1024 / 1024).toFixed(2)}MB (quality: ${quality})`)
    return { buffer: result, resized: true }

  } catch (error) {
    console.error('[BFF] Failed to resize image:', safeName, error)
    return { buffer, resized: false }
  }
}

/**
 * Resize base64-encoded images inside an AG-UI messages array that exceed 5MB.
 * Handles both AG-UI "binary" content parts (from frontend) and "image" content parts.
 */
async function processAguiMessagesImages(
  messages: Array<{ role?: string; content?: unknown }>
): Promise<void> {
  for (const msg of messages) {
    if (!Array.isArray(msg.content)) continue
    for (const part of msg.content as any[]) {
      // AG-UI binary parts with image MIME type (sent by web and mobile frontends)
      if (part.type === 'binary' && typeof part.mimeType === 'string' && part.mimeType.startsWith('image/') && part.data) {
        const raw = Buffer.from(part.data, 'base64')
        const { buffer: resized, resized: didResize } = await resizeImageIfNeeded(raw, part.mimeType, part.filename || 'image')
        if (didResize) {
          part.data = resized.toString('base64')
        }
        continue
      }
      // Legacy "image" content parts (Anthropic-style)
      if (part.type === 'image') {
        const source = part.source
        if (!source || source.type !== 'base64' || !source.data) continue
        const raw = Buffer.from(source.data, 'base64')
        const mime: string = source.mediaType || 'image/jpeg'
        const { buffer: resized, resized: didResize } = await resizeImageIfNeeded(raw, mime, 'inline')
        if (didResize) {
          source.data = resized.toString('base64')
        }
      }
    }
  }
}

// Check if running in local mode
const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

export const runtime = 'nodejs'
export const maxDuration = 1800 // 30 minutes for long-running agent tasks (self-hosted, no Vercel limits)

export async function POST(request: NextRequest) {
  try {
    // Parse JSON as AG-UI RunAgentInput: { threadId, runId, messages, tools, state }
    const body = await request.json()

    const threadIdFromBody: string | undefined = body.threadId
    const runIdFromBody: string | undefined = body.runId
    const aguiMessages: Array<{ id?: string; role?: string; content?: unknown }> = body.messages ?? []

    // Per-request config lives in body.state (replaces former top-level fields)
    const state = body.state ?? {}
    let model_id: string | undefined = state.model_id
    let temperature: number | undefined = state.temperature
    let request_type: string | undefined = state.request_type
    let selected_artifact_id: string | undefined = state.selected_artifact_id
    let system_prompt: string | undefined = state.system_prompt
    // Clients without an OAuth callback page (mobile) opt out of 3LO skills.
    // Only an explicit false disables them, so web keeps the default.
    const allow_user_federation: boolean = state.allow_user_federation !== false
    // Concise mode: the client sends a flag, the prompt text is added here so it
    // stays out of the browser bundle and can be tuned without a frontend build.
    const concise_mode: boolean = state.concise_mode === true

    // Extract user message from the last element of body.messages.
    // content may be a string (text-only) or an InputContentPart[] (multimodal).
    let message: string
    const lastMsg = aguiMessages[aguiMessages.length - 1]
    const lastContent = lastMsg?.content
    if (Array.isArray(lastContent)) {
      const textPart = lastContent.find((p: any) => p.type === 'text')
      message = (textPart as any)?.text ?? ''
    } else {
      message = (lastContent as string) ?? ''
    }

    // Message is required unless the request contains multimodal content (images/docs only).
    if (!message && !threadIdFromBody) {
      return new Response(
        JSON.stringify({ error: 'Message is required' }),
        { status: 400, headers: { 'Content-Type': 'application/json' } }
      )
    }

    // Extract user from Cognito JWT token in Authorization header
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    // Forward the verified caller token only as the Runtime Authorization header.
    const authHeader = request.headers.get('authorization') || ''
    const authToken = authHeader.startsWith('Bearer ') ? authHeader : ''

    // Get or generate session ID (user-specific)
    // For the AG-UI JSON path, threadId from RunAgentInput is the authoritative session identifier
    let { sessionId } = getSessionId(request, userId)
    if (threadIdFromBody) {
      sessionId = threadIdFromBody
    }

    // Ensure session exists in storage (creates if not exists)
    const { isNew: isNewSession } = await ensureSessionExists(userId, sessionId, {
      title: message.length > 50 ? message.substring(0, 47) + '...' : message,
    })

    console.log(`[BFF] User: ${userId}, Session: ${sessionId}${isNewSession ? ' (new)' : ''}`)

    // Helper function to get current date in US Pacific timezone
    function getCurrentDatePacific(): string {
      try {
        const now = new Date()

        // Get individual date/time components for Pacific timezone
        const year = now.toLocaleString('en-US', { timeZone: 'America/Los_Angeles', year: 'numeric' })
        const month = now.toLocaleString('en-US', { timeZone: 'America/Los_Angeles', month: '2-digit' })
        const day = now.toLocaleString('en-US', { timeZone: 'America/Los_Angeles', day: '2-digit' })
        const weekday = now.toLocaleString('en-US', { timeZone: 'America/Los_Angeles', weekday: 'long' })
        const hour = now.toLocaleString('en-US', { timeZone: 'America/Los_Angeles', hour: '2-digit', hour12: false }).split(':')[0]

        // Determine PST or PDT (rough estimation: March-October is PDT)
        const monthNum = parseInt(month)
        const tzAbbr = (monthNum >= 3 && monthNum <= 10) ? 'PDT' : 'PST'

        // Format: "YYYY-MM-DD (Weekday) HH:00 TZ"
        return `${year}-${month}-${day} (${weekday}) ${hour}:00 ${tzAbbr}`
      } catch (error) {
        // Fallback to UTC
        const now = new Date()
        const isoDate = now.toISOString().split('T')[0]
        const weekday = now.toLocaleDateString('en-US', { weekday: 'long' })
        const hour = now.getUTCHours().toString().padStart(2, '0')
        return `${isoDate} (${weekday}) ${hour}:00 UTC`
      }
    }

    // Load model configuration from storage (only if not provided in request)
    const defaultModelId = model_id || 'openai.gpt-5.6-terra'

    let modelConfig = {
      model_id: defaultModelId,
      temperature: temperature ?? 0.5,
      system_prompt: "",
      caching_enabled: defaultModelId.toLowerCase().includes('claude')
    }

    // Only load global profile config if model_id was NOT provided in the request
    // When the frontend sends model_id/temperature, they represent per-session state
    if (!model_id) {
      if (IS_LOCAL) {
        try {
          const { getUserModelConfig } = await import('@/lib/local-tool-store')
          const config = getUserModelConfig(userId)
          console.log(`[BFF] Loaded model config for ${userId}:`, config)
          if (config) {
            // Update model and temperature
            if (config.model_id) {
              modelConfig.model_id = config.model_id
              modelConfig.caching_enabled = config.model_id.toLowerCase().includes('claude')
              console.log(`[BFF] Applied model_id: ${config.model_id}, caching: ${modelConfig.caching_enabled}`)
            }
            if (config.temperature !== undefined) {
              modelConfig.temperature = config.temperature
            }
          } else {
            console.log(`[BFF] No saved config found for ${userId}, using defaults`)
          }
        } catch (error) {
          console.error(`[BFF] Error loading config for ${userId}:`, error)
          // Use defaults
        }
      } else {
        // DynamoDB for all users (including anonymous)
        try {
          const { getUserProfile } = await import('@/lib/dynamodb-client')
          const profile = await getUserProfile(userId)
          if (profile?.preferences) {
            if (profile.preferences.defaultModel) {
              modelConfig.model_id = profile.preferences.defaultModel
              modelConfig.caching_enabled = profile.preferences.defaultModel.toLowerCase().includes('claude')
            }
            if (profile.preferences.defaultTemperature !== undefined) {
              modelConfig.temperature = profile.preferences.defaultTemperature
            }
          }
        } catch (error) {
          // Use defaults
        }
      }
    } else {
      console.log(`[BFF] Using request-provided model_id: ${model_id}, temperature: ${temperature}`)
    }

    // Use default system prompt (prompt selection feature removed)
    const basePrompt = ""

    // Add current date to system prompt (at the end)
    const currentDate = getCurrentDatePacific()
    modelConfig.system_prompt = `${basePrompt}\n\nCurrent date and time: ${currentDate}`
    console.log(`[BFF] Added current date to system prompt: ${currentDate}`)


    // Create a custom stream that:
    // 1. Immediately starts sending keep-alive (before AgentCore responds)
    // 2. Continues keep-alive during AgentCore processing
    // 3. Forwards AgentCore chunks as passthrough (no parsing or buffering)
    const stream = new ReadableStream({
      async start(controller) {
        const encoder = new TextEncoder()
        let lastActivityTime = Date.now()
        let keepAliveInterval: NodeJS.Timeout | null = null
        let agentStarted = false
        let clientDisconnected = false

        // Send initial keep-alive immediately to establish connection
        controller.enqueue(encoder.encode(`: connected ${new Date().toISOString()}\n\n`))

        // Start keep-alive interval (runs every 20 seconds)
        keepAliveInterval = setInterval(() => {
          if (clientDisconnected) return
          const now = Date.now()
          const timeSinceActivity = now - lastActivityTime

          if (timeSinceActivity >= 20000) {
            try {
              controller.enqueue(encoder.encode(`: keep-alive ${new Date().toISOString()}\n\n`))
              lastActivityTime = now
            } catch (err) {
              clientDisconnected = true
              if (keepAliveInterval) {
                clearInterval(keepAliveInterval)
                keepAliveInterval = null
              }
            }
          }
        }, 20000)

        // AbortController for browser session polling
        const pollingAbortController = new AbortController()
        let browserSessionPollingStarted = false

        // AbortController for AgentCore stream
        const agentCoreAbortController = new AbortController()

        // On client disconnect, abort the AgentCore reader.
        // Agent continues as a backend background task; resume reconnects to backend buffer.
        request.signal.addEventListener('abort', () => {
          console.log('[BFF] Client disconnected, aborting stream relay')
          clientDisconnected = true
          if (keepAliveInterval) {
            clearInterval(keepAliveInterval)
            keepAliveInterval = null
          }
          agentCoreAbortController.abort()
        })

        try {
          // Execute before hooks (session metadata, tool config, etc.)
          const hookManager = createDefaultHookManager()
          await hookManager.executeBeforeHooks({
            userId,
            sessionId,
            message,
            modelConfig,
          })

          // Merge system prompts: model config + user-provided (artifact context).
          //
          // Concise mode is deliberately NOT appended here. Appending a "be brief"
          // block left it competing with the runtime's base prompt, which mandates
          // prose over lists and a 1-2 sentence minimum per bullet; the model split
          // the difference and kept answering at length. The runtime now swaps its
          // own style sections instead, driven by the concise_mode flag below.
          const finalSystemPrompt = [modelConfig.system_prompt, system_prompt]
            .filter(Boolean)
            .join('\n\n')

          // Process inline images in AG-UI messages (resize if needed)
          if (aguiMessages.length > 0) {
            await processAguiMessagesImages(aguiMessages)
          }

          // Load user's disabled skills from DB
          let disabled_skills: string[] = []
          try {
            if (IS_LOCAL) {
              // Local mode: no disabled skills (all enabled)
            } else {
              const { getUserDisabledSkills } = await import('@/lib/dynamodb-client')
              disabled_skills = await getUserDisabledSkills(userId)
            }
          } catch (e) {
            console.warn('[BFF] Failed to load disabled skills:', e)
          }

          // Build AG-UI body with server-side config enriched into state
          const enrichedState: Record<string, any> = {
            user_id: userId,
            model_id: modelConfig.model_id,
            temperature: modelConfig.temperature,
            system_prompt: finalSystemPrompt,
            caching_enabled: modelConfig.caching_enabled,
            ...(selected_artifact_id && { selected_artifact_id }),
            ...(request_type && { request_type }),
            ...(disabled_skills.length > 0 && { disabled_skills }),
            ...(!allow_user_federation && { allow_user_federation: false }),
            ...(concise_mode && { concise_mode: true }),
          }

          const aguiBody = {
            thread_id: sessionId,
            run_id: runIdFromBody || crypto.randomUUID(),
            messages: aguiMessages,
            tools: [],
            context: [],
            state: enrichedState,
          }

          const agentStream = await invokeAgentCoreRuntime(
            aguiBody, userId, sessionId, authToken, agentCoreAbortController.signal
          )
          agentStarted = true

          // Simple byte passthrough — no parsing or buffering.
          // Backend ExecutionRegistry handles event buffering for resume.
          const reader = agentStream.getReader()

          while (true) {
            const { done, value } = await reader.read()
            if (done) break

            if (!clientDisconnected) {
              try {
                controller.enqueue(value)
                lastActivityTime = Date.now()
              } catch (err) {
                clientDisconnected = true
                console.log('[BFF] Client disconnected mid-stream')
                agentCoreAbortController.abort()
                break
              }
            } else {
              break
            }
          }

        } catch (error) {
          console.error('[BFF] Error:', error)
          const errorEvent = `data: ${JSON.stringify({
            type: 'error',
            message: error instanceof Error ? error.message : 'Unknown error',
            metadata: { session_id: sessionId }
          })}\n\n`
          try {
            if (!clientDisconnected) {
              controller.enqueue(encoder.encode(errorEvent))
            }
          } catch (err) {
            // Controller already closed, ignore
            console.log('[BFF] Controller closed, cannot send error event')
          }
        } finally {
          // Update session metadata after message processing
          try {
            let currentSession: any = null
            if (userId === 'anonymous') {
              if (IS_LOCAL) {
                const { getSession } = await import('@/lib/local-session-store')
                currentSession = getSession(userId, sessionId)
              }
            } else {
              if (IS_LOCAL) {
                const { getSession } = await import('@/lib/local-session-store')
                currentSession = getSession(userId, sessionId)
              } else {
                const { getSession: getDynamoSession } = await import('@/lib/dynamodb-client')
                currentSession = await getDynamoSession(userId, sessionId)
              }
            }

            if (currentSession) {
              const updates: any = {
                lastMessageAt: new Date().toISOString(),
                messageCount: (currentSession.messageCount || 0) + 1,
                // Save model and tool preferences for session restoration
                metadata: {
                  lastModel: modelConfig.model_id,
                  lastTemperature: modelConfig.temperature,
                  skillsEnabled: request_type === 'skill',
                },
              }

              // Save session metadata for all users (including anonymous in AWS)
              if (IS_LOCAL) {
                const { updateSession } = await import('@/lib/local-session-store')
                updateSession(userId, sessionId, updates)
              } else {
                const { updateSession: updateDynamoSession } = await import('@/lib/dynamodb-client')
                await updateDynamoSession(userId, sessionId, updates)
              }
            }
          } catch (updateError) {
            console.error('[BFF] Session update error:', updateError)
          }

          // Stop browser session polling
          if (browserSessionPollingStarted) {
            pollingAbortController.abort()
            console.log('[BFF] Stopped browser session polling')
          }

          if (keepAliveInterval) {
            clearInterval(keepAliveInterval)
          }
          if (!clientDisconnected) {
            try { controller.close() } catch { /* already closed */ }
          }
        }
      }
    })

    // Set headers for Server-Sent Events
    const headers = new Headers({
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      'X-Accel-Buffering': 'no',
      'X-Session-ID': sessionId,
      'X-Session-Is-New': isNewSession ? 'true' : 'false',
      'Connection': 'keep-alive'
    })

    // Return the stream
    return new Response(stream, { headers })

  } catch (error) {
    console.error('[BFF] Error in chat endpoint:', error)
    return new Response(
      JSON.stringify({
        error: 'Internal server error',
        message: error instanceof Error ? error.message : 'Unknown error'
      }),
      { status: 500, headers: { 'Content-Type': 'application/json' } }
    )
  }
}
