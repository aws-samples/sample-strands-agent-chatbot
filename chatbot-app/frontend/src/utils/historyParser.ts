/**
 * Parsing utilities for AgentCore Memory events
 * Extracted from conversation/history/route.ts for testability
 */

export interface ParsedMessage {
  id: string
  role: string
  content: any[]
  timestamp: string
  [key: string]: any
}

export interface AgentCoreEvent {
  eventId?: string
  eventTimestamp?: string | Date
  eventTime?: string
  payload?: Array<{
    conversational?: {
      content?: {
        text?: string
      }
    }
    blob?: string
  }>
}

export interface ParseResult {
  success: boolean
  message?: ParsedMessage
  error?: string
}

function eventTimestampString(event: AgentCoreEvent): string {
  const value = event.eventTimestamp ?? event.eventTime
  if (value instanceof Date) return value.toISOString()
  if (typeof value === 'string') return value
  return new Date().toISOString()
}

/**
 * Parse a conversational event (message < 9000 chars)
 * AgentCore stores short messages in conversational.content.text as JSON
 */
export function parseConversationalEvent(
  event: AgentCoreEvent,
  sessionId: string,
  msgIndex: number
): ParseResult {
  const payload = event.payload?.[0]

  if (!payload?.conversational) {
    return { success: false, error: 'Not a conversational event' }
  }

  const conv = payload.conversational
  const content = conv.content?.text || ''

  if (!content) {
    return { success: false, error: 'Empty conversational content' }
  }

  let parsed: any
  try {
    parsed = JSON.parse(content)
  } catch (e) {
    return { success: false, error: `Invalid JSON in conversational content: ${e}` }
  }

  if (!parsed.message) {
    return { success: false, error: 'Missing "message" key in parsed content' }
  }

  const message: ParsedMessage = {
    ...parsed.message,
    id: event.eventId || `msg-${sessionId}-${msgIndex}`,
    timestamp: eventTimestampString(event)
  }

  return { success: true, message }
}

/**
 * Parse a blob event (message >= 9000 chars)
 * AgentCore stores long messages in payload.blob as JSON tuple: ["message_json", "role"]
 */
export function parseBlobEvent(
  event: AgentCoreEvent,
  sessionId: string,
  msgIndex: number
): ParseResult {
  const payload = event.payload?.[0]

  if (!payload?.blob || typeof payload.blob !== 'string') {
    return { success: false, error: 'Not a valid blob event' }
  }

  let blobParsed: any
  try {
    blobParsed = JSON.parse(payload.blob)
  } catch (e) {
    return { success: false, error: `Invalid JSON in blob: ${e}` }
  }

  // Blob format from SDK: ["message_json", "role"] tuple
  if (!Array.isArray(blobParsed) || blobParsed.length < 1) {
    return { success: false, error: 'Blob is not in expected tuple format' }
  }

  let blobMessageData: any
  try {
    blobMessageData = JSON.parse(blobParsed[0])
  } catch (e) {
    return { success: false, error: `Invalid JSON in blob tuple: ${e}` }
  }

  if (!blobMessageData?.message) {
    return { success: false, error: 'Missing "message" key in blob data' }
  }

  const message: ParsedMessage = {
    ...blobMessageData.message,
    id: event.eventId || `msg-${sessionId}-${msgIndex}`,
    timestamp: eventTimestampString(event)
  }

  return { success: true, message }
}

/**
 * Parse a single AgentCore event (either conversational or blob)
 */
export function parseAgentCoreEvent(
  event: AgentCoreEvent,
  sessionId: string,
  msgIndex: number
): ParseResult {
  const payload = event.payload?.[0]

  if (!payload) {
    return { success: false, error: 'No payload in event' }
  }

  // Try conversational first (more common)
  if (payload.conversational) {
    return parseConversationalEvent(event, sessionId, msgIndex)
  }

  // Then try blob
  if (payload.blob && typeof payload.blob === 'string') {
    return parseBlobEvent(event, sessionId, msgIndex)
  }

  return { success: false, error: 'Event has neither conversational nor blob payload' }
}

/**
 * Parse multiple AgentCore events into messages
 * Events are expected to be in chronological order
 */
export function parseAgentCoreEvents(
  events: AgentCoreEvent[],
  sessionId: string
): ParsedMessage[] {
  const messages: ParsedMessage[] = []
  let msgIndex = 0

  for (const event of events) {
    const result = parseAgentCoreEvent(event, sessionId, msgIndex)
    if (result.success && result.message) {
      messages.push(result.message)
      msgIndex++
    }
  }

  return messages
}

/**
 * Merge session metadata into messages
 * Adds latency, tokenUsage, feedback, documents from session metadata
 */
export function mergeMessageMetadata(
  messages: ParsedMessage[],
  sessionMetadata: { messages?: Record<string, any> } | null
): ParsedMessage[] {
  if (!sessionMetadata?.messages) {
    return messages
  }

  return messages.map(msg => {
    const metadata = sessionMetadata.messages![msg.id]
    if (!metadata) {
      return msg
    }

    return {
      ...msg,
      ...(metadata.latency && { latencyMetrics: metadata.latency }),
      ...(metadata.tokenUsage && { tokenUsage: metadata.tokenUsage }),
      ...(metadata.feedback && { feedback: metadata.feedback }),
      ...(metadata.documents && { documents: metadata.documents })
    }
  })
}
