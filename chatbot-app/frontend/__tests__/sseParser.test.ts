import { describe, it, expect } from 'vitest'
import {
  parseSSELine,
  parseSSEData,
  parseSSEChunk,
  validateAGUIStreamEvent,
  createMockEvent,
  serializeToSSE
} from '@/utils/sseParser'
import type { AGUIStreamEvent } from '@/types/events'
import { EventType } from '@ag-ui/core'

describe('sseParser', () => {
  describe('parseSSELine', () => {
    it('should parse empty line', () => {
      const result = parseSSELine('')
      expect(result).toEqual({ type: 'empty', value: '' })
    })

    it('should parse comment line', () => {
      const result = parseSSELine(': this is a comment')
      expect(result).toEqual({ type: 'comment', value: 'this is a comment' })
    })

    it('should parse event line', () => {
      const result = parseSSELine('event: message')
      expect(result).toEqual({ type: 'event', value: 'message' })
    })

    it('should parse event line with extra whitespace', () => {
      const result = parseSSELine('event:   response  ')
      expect(result).toEqual({ type: 'event', value: 'response' })
    })

    it('should parse data line', () => {
      const result = parseSSELine('data: {"type":"response","text":"Hello"}')
      expect(result).toEqual({
        type: 'data',
        value: '{"type":"response","text":"Hello"}'
      })
    })

    it('should parse retry line', () => {
      const result = parseSSELine('retry: 3000')
      expect(result).toEqual({ type: 'retry', value: '3000' })
    })

    it('should treat unknown format as data', () => {
      const result = parseSSELine('some unknown format')
      expect(result).toEqual({ type: 'data', value: 'some unknown format' })
    })
  })

  describe('parseSSEData', () => {
    it('should return null for empty data', () => {
      expect(parseSSEData('')).toBeNull()
    })

    it('should parse valid JSON with type field', () => {
      const result = parseSSEData('{"type":"response","text":"Hello"}')
      expect(result).toEqual({ type: 'response', text: 'Hello' })
    })

    it('should return null for invalid JSON', () => {
      expect(parseSSEData('{invalid json}')).toBeNull()
    })

    it('should return null for JSON without type field', () => {
      expect(parseSSEData('{"data":"something"}')).toBeNull()
    })

    it('should parse complex event data', () => {
      const data = JSON.stringify({
        type: 'tool_use',
        toolUseId: 'tool-123',
        name: 'calculator',
        input: { expression: '2 + 2' }
      })

      const result = parseSSEData(data)
      expect(result).toEqual({
        type: 'tool_use',
        toolUseId: 'tool-123',
        name: 'calculator',
        input: { expression: '2 + 2' }
      })
    })
  })

  describe('parseSSEChunk', () => {
    it('should parse single event', () => {
      const chunk = 'data: {"type":"response","text":"Hi"}\n\n'

      const result = parseSSEChunk(chunk)

      expect(result.events).toHaveLength(1)
      expect(result.events[0]).toEqual({ type: 'response', text: 'Hi' })
      expect(result.errors).toHaveLength(0)
    })

    it('should parse multiple events', () => {
      const chunk = [
        'data: {"type":"init","message":"Starting"}',
        '',
        'data: {"type":"response","text":"Hello"}',
        '',
        'data: {"type":"complete","message":"Done"}',
        ''
      ].join('\n')

      const result = parseSSEChunk(chunk)

      expect(result.events).toHaveLength(3)
      expect(result.events[0].type).toBe('init')
      expect(result.events[1].type).toBe('response')
      expect(result.events[2].type).toBe('complete')
    })

    it('should handle event type line followed by data', () => {
      const chunk = 'event: message\ndata: {"type":"response","text":"Hi"}\n\n'

      const result = parseSSEChunk(chunk)

      expect(result.events).toHaveLength(1)
      expect(result.events[0].type).toBe('response')
    })

    it('should concatenate multiple data lines', () => {
      // SSE spec allows multiple data lines - they get concatenated with newlines
      // Our parser concatenates them, but the resulting string has a newline in the middle
      // which can make JSON parsing succeed if the split happens at valid JSON boundaries
      const chunk = [
        'data: {"type":"response",',
        'data: "text":"Hello World"}',
        ''
      ].join('\n')

      const result = parseSSEChunk(chunk)

      // The parser concatenates: '{"type":"response",\n"text":"Hello World"}'
      // JSON.parse handles newlines in strings, so this actually parses successfully
      expect(result.events).toHaveLength(1)
      expect(result.events[0].type).toBe('response')
      expect((result.events[0] as any).text).toBe('Hello World')
    })

    it('should collect errors for invalid events', () => {
      const chunk = [
        'data: {"type":"response","text":"Valid"}',
        '',
        'data: {invalid json}',
        '',
        'data: {"type":"complete","message":"Done"}',
        ''
      ].join('\n')

      const result = parseSSEChunk(chunk)

      expect(result.events).toHaveLength(2)
      expect(result.errors).toHaveLength(1)
      expect(result.errors[0]).toContain('Failed to parse SSE data')
    })

    it('should ignore comment lines', () => {
      const chunk = [
        ': this is a comment',
        'data: {"type":"response","text":"Hi"}',
        ''
      ].join('\n')

      const result = parseSSEChunk(chunk)

      expect(result.events).toHaveLength(1)
      expect(result.events[0].type).toBe('response')
    })

    it('should handle empty chunk', () => {
      const result = parseSSEChunk('')

      expect(result.events).toHaveLength(0)
      expect(result.errors).toHaveLength(0)
    })

    it('should handle chunk with only whitespace', () => {
      const result = parseSSEChunk('  \n\n  ')

      expect(result.events).toHaveLength(0)
      expect(result.errors).toHaveLength(0)
    })
  })

  describe('validateAGUIStreamEvent', () => {
    it('should validate RUN_STARTED event', () => {
      const valid = { type: EventType.RUN_STARTED, threadId: 'thread-1', runId: 'run-1' } as unknown as AGUIStreamEvent
      const missingThreadId = { type: EventType.RUN_STARTED, runId: 'run-1' } as unknown as AGUIStreamEvent

      expect(validateAGUIStreamEvent(valid).valid).toBe(true)
      expect(validateAGUIStreamEvent(missingThreadId).valid).toBe(false)
      expect(validateAGUIStreamEvent(missingThreadId).errors[0]).toContain('threadId')
    })

    it('should validate RUN_ERROR event', () => {
      const valid = { type: EventType.RUN_ERROR, message: 'Something went wrong' } as unknown as AGUIStreamEvent
      const invalid = { type: EventType.RUN_ERROR } as unknown as AGUIStreamEvent

      expect(validateAGUIStreamEvent(valid).valid).toBe(true)
      expect(validateAGUIStreamEvent(invalid).valid).toBe(false)
    })

    it('should validate TEXT_MESSAGE_CONTENT event', () => {
      const valid = { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'msg-1', delta: 'Hello' } as unknown as AGUIStreamEvent
      const missingDelta = { type: EventType.TEXT_MESSAGE_CONTENT, messageId: 'msg-1' } as unknown as AGUIStreamEvent

      expect(validateAGUIStreamEvent(valid).valid).toBe(true)
      expect(validateAGUIStreamEvent(missingDelta).valid).toBe(false)
      expect(validateAGUIStreamEvent(missingDelta).errors[0]).toContain('delta')
    })

    it('should validate TOOL_CALL_START event', () => {
      const valid = { type: EventType.TOOL_CALL_START, toolCallId: 'tc-1', toolCallName: 'calculator' } as unknown as AGUIStreamEvent
      const missingName = { type: EventType.TOOL_CALL_START, toolCallId: 'tc-1' } as unknown as AGUIStreamEvent

      expect(validateAGUIStreamEvent(valid).valid).toBe(true)
      expect(validateAGUIStreamEvent(missingName).valid).toBe(false)
      expect(validateAGUIStreamEvent(missingName).errors[0]).toContain('toolCallName')
    })

    it('should validate TOOL_CALL_RESULT event', () => {
      const valid = { type: EventType.TOOL_CALL_RESULT, toolCallId: 'tc-1', messageId: 'msg-1', content: 'result' } as unknown as AGUIStreamEvent
      const invalid = { type: EventType.TOOL_CALL_RESULT, messageId: 'msg-1', content: 'result' } as unknown as AGUIStreamEvent

      expect(validateAGUIStreamEvent(valid).valid).toBe(true)
      expect(validateAGUIStreamEvent(invalid).valid).toBe(false)
    })

    it('should validate CUSTOM event', () => {
      const valid = { type: EventType.CUSTOM, name: 'reasoning', value: {} } as unknown as AGUIStreamEvent
      const missingName = { type: EventType.CUSTOM, value: {} } as unknown as AGUIStreamEvent

      expect(validateAGUIStreamEvent(valid).valid).toBe(true)
      expect(validateAGUIStreamEvent(missingName).valid).toBe(false)
      expect(validateAGUIStreamEvent(missingName).errors[0]).toContain('name')
    })

    it('should reject unknown event types at the protocol boundary', () => {
      const unknown = { type: 'unknown_type' } as unknown as AGUIStreamEvent

      const result = validateAGUIStreamEvent(unknown)
      expect(result.valid).toBe(false)
      expect(result.errors.length).toBeGreaterThan(0)
    })
  })

  describe('createMockEvent', () => {
    it('should create event with correct type', () => {
      const event = createMockEvent('CUSTOM')
      expect(event.type).toBe('CUSTOM')
    })

    it('should create event with overrides', () => {
      const event = createMockEvent('CUSTOM', { name: 'reasoning', value: { text: 'Analyzing...' } })
      expect(event.type).toBe('CUSTOM')
      expect(event.name).toBe('reasoning')
    })

    it('should create TOOL_CALL_START event', () => {
      const event = createMockEvent('TOOL_CALL_START', {
        toolCallId: 'tool-abc',
        toolCallName: 'search',
      })
      expect(event.type).toBe('TOOL_CALL_START')
      expect(event.toolCallId).toBe('tool-abc')
      expect(event.toolCallName).toBe('search')
    })

    it('should create RUN_FINISHED event with usage', () => {
      const event = createMockEvent('RUN_FINISHED', {
        threadId: 'thread-1',
        runId: 'run-1',
      })
      expect(event.type).toBe('RUN_FINISHED')
      expect(event.threadId).toBe('thread-1')
    })
  })

  describe('serializeToSSE', () => {
    it('should serialize event without event name', () => {
      const event = { type: 'response', text: 'Hello' } as unknown as AGUIStreamEvent
      const result = serializeToSSE(event)

      expect(result).toBe('data: {"type":"response","text":"Hello"}\n\n')
    })

    it('should serialize event with event name', () => {
      const event = { type: 'response', text: 'Hello' } as unknown as AGUIStreamEvent
      const result = serializeToSSE(event, 'message')

      expect(result).toBe('event: message\ndata: {"type":"response","text":"Hello"}\n\n')
    })

    it('should handle complex event data', () => {
      const event = {
        type: 'tool_result',
        toolUseId: 'tool-123',
        result: 'success',
        images: [{ format: 'png', data: 'base64...' }]
      } as unknown as AGUIStreamEvent

      const result = serializeToSSE(event)
      const parsed = parseSSEChunk(result)

      expect(parsed.events).toHaveLength(1)
      expect(parsed.events[0]).toEqual(event)
    })

    it('should create parseable SSE format', () => {
      const event = createMockEvent('complete', { message: 'All done!' })
      const serialized = serializeToSSE(event)
      const { events } = parseSSEChunk(serialized)

      expect(events).toHaveLength(1)
      expect(events[0].type).toBe('complete')
      expect((events[0] as any).message).toBe('All done!')
    })
  })

  // ============================================================
  // Interrupt Event Parsing Tests
  // ============================================================

  describe('interrupt event parsing', () => {
    it('should parse research approval interrupt event', () => {
      const chunk = `data: {"type":"interrupt","interrupts":[{"id":"chatbot-research-001","name":"chatbot-research-approval","reason":{"tool_name":"research_agent","plan":"Step 1: Search\\nStep 2: Analyze"}}]}\n\n`

      const result = parseSSEChunk(chunk)

      expect(result.events).toHaveLength(1)
      expect(result.events[0].type).toBe('interrupt')

      const interruptEvent = result.events[0] as any
      expect(interruptEvent.interrupts).toHaveLength(1)
      expect(interruptEvent.interrupts[0].id).toBe('chatbot-research-001')
      expect(interruptEvent.interrupts[0].name).toBe('chatbot-research-approval')
    })


    it('should reject legacy lowercase interrupt events', () => {
      // interrupt is a legacy/custom type — validator allows all unknown types
      const missingInterrupts = { type: 'interrupt' } as unknown as AGUIStreamEvent
      const withInterrupts = {
        type: 'interrupt',
        interrupts: [{ id: 'int-1', name: 'chatbot-research-approval' }]
      } as unknown as AGUIStreamEvent

      expect(validateAGUIStreamEvent(missingInterrupts).valid).toBe(false)
      expect(validateAGUIStreamEvent(withInterrupts).valid).toBe(false)
    })

    it('should create mock interrupt event', () => {
      const event = createMockEvent('interrupt', {
        interrupts: [{
          id: 'mock-interrupt-001',
          name: 'chatbot-research-approval',
          reason: { plan: 'Test plan' }
        }]
      })

      expect(event.type).toBe('interrupt')
      expect((event as any).interrupts).toHaveLength(1)
      expect((event as any).interrupts[0].id).toBe('mock-interrupt-001')
    })

    it('should serialize and parse interrupt event correctly', () => {
      const originalEvent = {
        type: 'interrupt',
        interrupts: [{
          id: 'roundtrip-001',
          name: 'chatbot-research-approval',
          reason: {
            tool_name: 'research_agent',
            plan: 'Step 1: Do this\nStep 2: Do that'
          }
        }]
      } as unknown as AGUIStreamEvent

      const serialized = serializeToSSE(originalEvent)
      const { events, errors } = parseSSEChunk(serialized)

      expect(errors).toHaveLength(0)
      expect(events).toHaveLength(1)
      expect(events[0].type).toBe('interrupt')

      const parsed = events[0] as any
      expect(parsed.interrupts[0].id).toBe('roundtrip-001')
      expect(parsed.interrupts[0].reason.plan).toContain('Step 1')
    })

    it('should handle interrupt in streaming conversation flow', () => {
      // Simulate: RUN_STARTED -> TOOL_CALL_START -> interrupt (HITL pause)
      const events = [
        `data: {"type":"RUN_STARTED","threadId":"t-1","runId":"r-1"}\n\n`,
        `data: {"type":"TOOL_CALL_START","toolCallId":"tc-001","toolCallName":"research_agent"}\n\n`,
        `data: {"type":"CUSTOM","name":"interrupt","value":{"interrupts":[{"id":"int-001","name":"chatbot-research-approval","reason":{"plan":"Research AI trends"}}]}}\n\n`
      ].join('')

      const result = parseSSEChunk(events)

      expect(result.events).toHaveLength(3)
      expect(result.events[0].type).toBe('RUN_STARTED')
      expect(result.events[1].type).toBe('TOOL_CALL_START')
      expect(result.events[2].type).toBe('CUSTOM')
    })
  })

  describe('round-trip parsing', () => {
    it('should handle full AG-UI streaming conversation simulation', () => {
      const events: AGUIStreamEvent[] = [
        createMockEvent('RUN_STARTED', { threadId: 't-1', runId: 'r-1' }),
        createMockEvent('TEXT_MESSAGE_START', { messageId: 'msg-1', role: 'assistant' }),
        createMockEvent('TEXT_MESSAGE_CONTENT', { messageId: 'msg-1', delta: 'Let me calculate...' }),
        createMockEvent('TOOL_CALL_START', { toolCallId: 'tc-1', toolCallName: 'calculator' }),
        createMockEvent('TOOL_CALL_ARGS', { toolCallId: 'tc-1', delta: '{"expression":"2+2"}' }),
        createMockEvent('TOOL_CALL_END', { toolCallId: 'tc-1' }),
        createMockEvent('TOOL_CALL_RESULT', { toolCallId: 'tc-1', content: '4' }),
        createMockEvent('TEXT_MESSAGE_CONTENT', { messageId: 'msg-1', delta: 'The answer is 4' }),
        createMockEvent('TEXT_MESSAGE_END', { messageId: 'msg-1' }),
        createMockEvent('RUN_FINISHED', { threadId: 't-1', runId: 'r-1' }),
      ]

      // Serialize all events
      const sseStream = events.map(e => serializeToSSE(e)).join('')

      // Parse them back
      const { events: parsed, errors } = parseSSEChunk(sseStream)

      expect(errors).toHaveLength(0)
      expect(parsed).toHaveLength(events.length)

      // Verify each event
      parsed.forEach((parsedEvent, i) => {
        expect(parsedEvent.type).toBe(events[i].type)
      })
    })
  })
})
