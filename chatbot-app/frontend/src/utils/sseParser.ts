/**
 * SSE (Server-Sent Events) parsing utilities
 * Extracted for testability from streaming hooks
 */

import type { AGUIStreamEvent } from '@/types/events'
import { EventSchemas, EventType } from '@ag-ui/core'

/**
 * Parse a single SSE line into event type and data
 * SSE format: "event: type\ndata: json\n\n"
 */
export interface SSELine {
  type: 'event' | 'data' | 'comment' | 'retry' | 'empty'
  value: string
}

export function parseSSELine(line: string): SSELine {
  if (line === '') {
    return { type: 'empty', value: '' }
  }

  if (line.startsWith(':')) {
    return { type: 'comment', value: line.slice(1).trim() }
  }

  if (line.startsWith('event:')) {
    return { type: 'event', value: line.slice(6).trim() }
  }

  if (line.startsWith('data:')) {
    return { type: 'data', value: line.slice(5).trim() }
  }

  if (line.startsWith('retry:')) {
    return { type: 'retry', value: line.slice(6).trim() }
  }

  // Unknown line format - treat as data
  return { type: 'data', value: line }
}

/**
 * Parse SSE data into a AGUIStreamEvent
 * Returns null if parsing fails
 */
export function parseSSEData(data: string): AGUIStreamEvent | null {
  if (!data) {
    return null
  }

  try {
    const parsed = JSON.parse(data)

    // Validate that parsed object has a type
    if (!parsed.type) {
      return null
    }

    return parsed as AGUIStreamEvent
  } catch (e) {
    return null
  }
}

/**
 * Parse multiple SSE lines into events
 * Handles the SSE protocol where events are separated by double newlines
 */
export interface ParsedSSEChunk {
  events: AGUIStreamEvent[]
  errors: string[]
}

export function parseSSEChunk(chunk: string): ParsedSSEChunk {
  const events: AGUIStreamEvent[] = []
  const errors: string[] = []

  // Split by double newlines to get individual SSE messages
  const messages = chunk.split('\n\n').filter(msg => msg.trim() !== '')

  for (const message of messages) {
    const lines = message.split('\n')
    let eventType = ''
    let eventData = ''

    for (const line of lines) {
      const parsed = parseSSELine(line)

      switch (parsed.type) {
        case 'event':
          eventType = parsed.value
          break
        case 'data':
          // SSE allows multiple data lines; concatenate them
          eventData += (eventData ? '\n' : '') + parsed.value
          break
        case 'comment':
        case 'retry':
        case 'empty':
          // Ignore these for event parsing
          break
      }
    }

    if (eventData) {
      const event = parseSSEData(eventData)
      if (event) {
        events.push(event)
      } else {
        errors.push(`Failed to parse SSE data: ${eventData.slice(0, 100)}`)
      }
    }
  }

  return { events, errors }
}

/**
 * Validate a AGUIStreamEvent has required fields based on its type
 */
export function validateAGUIStreamEvent(event: AGUIStreamEvent): { valid: boolean; errors: string[] } {
  const schemaResult = EventSchemas.safeParse(event)
  if (!schemaResult.success) {
    return {
      valid: false,
      errors: schemaResult.error.issues.map(issue => {
        const path = issue.path.length > 0 ? `${issue.path.join('.')}: ` : ''
        return `${path}${issue.message}`
      }),
    }
  }

  const errors: string[] = []
  switch (event.type) {
    case EventType.RUN_STARTED:
    case EventType.RUN_FINISHED:
      if (typeof event.threadId !== 'string') {
        errors.push(`${event.type} event missing "threadId" field`)
      }
      if (typeof event.runId !== 'string') {
        errors.push(`${event.type} event missing "runId" field`)
      }
      break

    case EventType.RUN_ERROR:
      if (typeof event.message !== 'string') {
        errors.push('RUN_ERROR event missing "message" field')
      }
      break

    case EventType.TEXT_MESSAGE_START:
    case EventType.TEXT_MESSAGE_END:
      if (typeof event.messageId !== 'string') {
        errors.push(`${event.type} event missing "messageId" field`)
      }
      break

    case EventType.TEXT_MESSAGE_CONTENT:
      if (typeof event.messageId !== 'string') {
        errors.push('TEXT_MESSAGE_CONTENT event missing "messageId" field')
      }
      if (typeof event.delta !== 'string') {
        errors.push('TEXT_MESSAGE_CONTENT event missing "delta" field')
      }
      break

    case EventType.TOOL_CALL_START:
      if (typeof event.toolCallId !== 'string') {
        errors.push('TOOL_CALL_START event missing "toolCallId" field')
      }
      if (typeof event.toolCallName !== 'string') {
        errors.push('TOOL_CALL_START event missing "toolCallName" field')
      }
      break

    case EventType.TOOL_CALL_ARGS:
      if (typeof event.toolCallId !== 'string') {
        errors.push('TOOL_CALL_ARGS event missing "toolCallId" field')
      }
      if (typeof event.delta !== 'string') {
        errors.push('TOOL_CALL_ARGS event missing "delta" field')
      }
      break

    case EventType.TOOL_CALL_END:
      if (typeof event.toolCallId !== 'string') {
        errors.push('TOOL_CALL_END event missing "toolCallId" field')
      }
      break

    case EventType.TOOL_CALL_RESULT:
      if (typeof event.toolCallId !== 'string') {
        errors.push('TOOL_CALL_RESULT event missing "toolCallId" field')
      }
      break

    case EventType.CUSTOM:
      if (typeof event.name !== 'string') {
        errors.push('CUSTOM event missing "name" field')
      }
      break
  }

  return { valid: errors.length === 0, errors }
}

/**
 * Create a mock AGUIStreamEvent for testing purposes.
 */
export function createMockEvent(
  type: string,
  overrides: Record<string, any> = {}
): any {
  return { type, ...overrides }
}

/**
 * Serialize a AGUIStreamEvent to SSE format
 */
export function serializeToSSE(event: AGUIStreamEvent, eventName?: string): string {
  const lines: string[] = []

  if (eventName) {
    lines.push(`event: ${eventName}`)
  }

  lines.push(`data: ${JSON.stringify(event)}`)
  lines.push('')  // Empty line to end the message
  lines.push('')  // Second empty line for SSE separator

  return lines.join('\n')
}
