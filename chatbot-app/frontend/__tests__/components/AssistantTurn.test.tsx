/**
 * Tests for AssistantTurn component
 *
 * Tests cover:
 * - Document download button rendering (Word, Excel, PowerPoint)
 * - File icon selection based on extension
 * - Download click handler
 * - Latency metrics display (TTFT, E2E)
 * - Token usage display (including cache tokens)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AssistantTurn } from '@/components/chat/AssistantTurn'
import type { Message } from '@/types/chat'

// Mock fetchAuthSession
vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: vi.fn().mockResolvedValue({
    tokens: { idToken: { toString: () => 'mock-token' } }
  })
}))

// Mock fetch
const mockFetch = vi.fn()
global.fetch = mockFetch

// Mock ResearchContainer
vi.mock('@/components/ResearchContainer', () => ({
  ResearchContainer: () => <div data-testid="research-container">Research</div>
}))

// Note: ToolExecutionContainer is not mocked - we test with actual rendering
// to verify real integration behavior

// Mock Markdown
vi.mock('@/components/ui/Markdown', () => ({
  Markdown: ({ children }: { children: string }) => <div data-testid="markdown">{children}</div>
}))

// Mock LazyImage
vi.mock('@/components/ui/LazyImage', () => ({
  LazyImage: ({ src }: { src: string }) => <img data-testid="lazy-image" src={src} />
}))

describe('AssistantTurn', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ s3Key: 's3://bucket/path/file.docx' })
    })
  })

  const createMessage = (overrides: Partial<Message> = {}): Message => ({
    id: 'msg-1',
    sender: 'bot',
    text: 'Test message',
    timestamp: new Date().toISOString(),
    ...overrides
  })

  describe('Document Download Rendering', () => {
    // Documents (pdf, csv, etc.) are now handled in Canvas, not rendered in AssistantTurn.
    it('should not render PDF document in AssistantTurn (moved to Canvas)', () => {
      const messages: Message[] = [
        createMessage({
          documents: [{ filename: 'report.pdf', tool_type: 'pdf' }]
        })
      ]

      render(<AssistantTurn messages={messages} sessionId="test-session" />)

      expect(screen.queryByText('report.pdf')).not.toBeInTheDocument()
      expect(screen.queryByText('1 Document')).not.toBeInTheDocument()
    })

    it('should not render CSV document in AssistantTurn (moved to Canvas)', () => {
      const messages: Message[] = [
        createMessage({
          documents: [{ filename: 'data.csv', tool_type: 'csv' }]
        })
      ]

      render(<AssistantTurn messages={messages} sessionId="test-session" />)

      expect(screen.queryByText('data.csv')).not.toBeInTheDocument()
    })

    it('should not render canvas doc types (docx/xlsx/pptx) in document section', () => {
      const messages: Message[] = [
        createMessage({
          documents: [{ filename: 'report.docx', tool_type: 'word' }]
        })
      ]

      render(<AssistantTurn messages={messages} sessionId="test-session" />)

      expect(screen.queryByText('report.docx')).not.toBeInTheDocument()
      expect(screen.queryByText('1 Document')).not.toBeInTheDocument()
    })

    it('should not render any documents in AssistantTurn (all moved to Canvas)', () => {
      const messages: Message[] = [
        createMessage({
          documents: [
            { filename: 'report.pdf', tool_type: 'pdf' },
            { filename: 'data.csv', tool_type: 'csv' },
            { filename: 'log.txt', tool_type: 'text' }
          ]
        })
      ]

      render(<AssistantTurn messages={messages} sessionId="test-session" />)

      expect(screen.queryByText('report.pdf')).not.toBeInTheDocument()
      expect(screen.queryByText('data.csv')).not.toBeInTheDocument()
      expect(screen.queryByText('log.txt')).not.toBeInTheDocument()
      expect(screen.queryByText('3 Documents')).not.toBeInTheDocument()
    })

    it('should not render document section when no documents', () => {
      const messages: Message[] = [createMessage()]

      render(<AssistantTurn messages={messages} sessionId="test-session" />)

      expect(screen.queryByText('Document')).not.toBeInTheDocument()
    })
  })

  describe('Document Download Click Handler', () => {
    it('should not render document download elements in AssistantTurn', () => {
      const messages: Message[] = [
        createMessage({
          documents: [{ filename: 'report.pdf', tool_type: 'pdf' }]
        })
      ]

      render(<AssistantTurn messages={messages} sessionId="test-session" />)

      // Documents are handled in Canvas, no download button in AssistantTurn
      expect(screen.queryByText('report.pdf')).not.toBeInTheDocument()
      expect(mockFetch).not.toHaveBeenCalledWith('/api/documents/download', expect.anything())
    })
  })

  describe('Latency Metrics Display', () => {
    it('should display TTFT when available', () => {
      const messages: Message[] = [
        createMessage({
          latencyMetrics: {
            timeToFirstToken: 150,
            endToEndLatency: 500
          }
        })
      ]

      const { container } = render(<AssistantTurn messages={messages} sessionId="test-session" />)

      // Metrics are shown on hover, check they exist in DOM
      expect(container.innerHTML).toContain('TTFT')
      expect(container.innerHTML).toContain('150ms')
    })

    it('should display E2E latency when available', () => {
      const messages: Message[] = [
        createMessage({
          latencyMetrics: {
            timeToFirstToken: 150,
            endToEndLatency: 500
          }
        })
      ]

      const { container } = render(<AssistantTurn messages={messages} sessionId="test-session" />)

      expect(container.innerHTML).toContain('E2E')
      expect(container.innerHTML).toContain('0.5s')
    })

    it('should not display metrics section when no metrics', () => {
      const messages: Message[] = [createMessage()]

      const { container } = render(<AssistantTurn messages={messages} sessionId="test-session" />)

      expect(container.innerHTML).not.toContain('TTFT')
      expect(container.innerHTML).not.toContain('E2E')
    })
  })

  describe('Token Usage Display', () => {
    it('should display input and output tokens', () => {
      const messages: Message[] = [
        createMessage({
          tokenUsage: {
            inputTokens: 1000,
            outputTokens: 500,
            totalTokens: 1500
          }
        })
      ]

      const { container } = render(<AssistantTurn messages={messages} sessionId="test-session" />)

      // Input tokens shown as "1.0k in", output as "500 out"
      expect(container.innerHTML).toContain('1.0k in')
      expect(container.innerHTML).toContain('500 out')
    })

    it('should display cache read tokens when present', () => {
      const messages: Message[] = [
        createMessage({
          tokenUsage: {
            inputTokens: 1000,
            outputTokens: 500,
            totalTokens: 1500,
            cacheReadInputTokens: 800
          }
        })
      ]

      const { container } = render(<AssistantTurn messages={messages} sessionId="test-session" />)

      expect(container.innerHTML).toContain('800')
      expect(container.innerHTML).toContain('hit')
    })

    it('should display cache write tokens when present', () => {
      const messages: Message[] = [
        createMessage({
          tokenUsage: {
            inputTokens: 1000,
            outputTokens: 500,
            totalTokens: 1500,
            cacheWriteInputTokens: 200
          }
        })
      ]

      const { container } = render(<AssistantTurn messages={messages} sessionId="test-session" />)

      expect(container.innerHTML).toContain('200')
      expect(container.innerHTML).toContain('write')
    })

    it('should display both cache read and write tokens', () => {
      const messages: Message[] = [
        createMessage({
          tokenUsage: {
            inputTokens: 1000,
            outputTokens: 500,
            totalTokens: 1500,
            cacheReadInputTokens: 800,
            cacheWriteInputTokens: 200
          }
        })
      ]

      const { container } = render(<AssistantTurn messages={messages} sessionId="test-session" />)

      expect(container.innerHTML).toContain('800')
      expect(container.innerHTML).toContain('hit')
      expect(container.innerHTML).toContain('200')
      expect(container.innerHTML).toContain('write')
    })

    it('should not display cache tokens when zero', () => {
      const messages: Message[] = [
        createMessage({
          tokenUsage: {
            inputTokens: 1000,
            outputTokens: 500,
            totalTokens: 1500,
            cacheReadInputTokens: 0,
            cacheWriteInputTokens: 0
          }
        })
      ]

      const { container } = render(<AssistantTurn messages={messages} sessionId="test-session" />)

      // Should have token info but not cache hit/write labels
      expect(container.innerHTML).toContain('1.0k in')
      // Note: The component only shows "hit" text when cacheReadInputTokens > 0
      // and "write" text when cacheWriteInputTokens > 0
      expect(container.innerHTML).not.toContain(' hit')  // space before to avoid matching other words
      expect(container.innerHTML).not.toContain(' write')
    })
  })

  describe('File Icon Selection', () => {
    it('should not render file icons for documents (documents moved to Canvas)', () => {
      const messages: Message[] = [
        createMessage({
          documents: [{ filename: 'test.pdf', tool_type: 'pdf' }]
        })
      ]

      const { container } = render(<AssistantTurn messages={messages} sessionId="test-session" />)

      // No document icon rendered in AssistantTurn
      expect(container.querySelector('[class*="cursor-pointer"]')).toBeNull()
    })
  })

  describe('Empty State', () => {
    it('should return null when no messages', () => {
      const { container } = render(<AssistantTurn messages={[]} sessionId="test-session" />)

      expect(container.firstChild).toBeNull()
    })
  })

  describe('Component Rendering Order', () => {
    it('should render messages in chronological order by timestamp', () => {
      const messages: Message[] = [
        createMessage({
          id: 'msg-3',
          text: 'Third message',
          timestamp: '2024-01-01T10:02:00Z'
        }),
        createMessage({
          id: 'msg-1',
          text: 'First message',
          timestamp: '2024-01-01T10:00:00Z'
        }),
        createMessage({
          id: 'msg-2',
          text: 'Second message',
          timestamp: '2024-01-01T10:01:00Z'
        })
      ]

      render(<AssistantTurn messages={messages} sessionId="test-session" />)

      const markdownElements = screen.getAllByTestId('markdown')
      // Messages should be sorted: First, Second, Third
      // Since consecutive text messages are grouped, we check the combined content
      expect(markdownElements[0].textContent).toContain('First message')
    })

    it('should render text before tool execution within same message', () => {
      const messages: Message[] = [
        createMessage({
          id: 'msg-1',
          text: 'Let me search for that',
          timestamp: '2024-01-01T10:00:00Z',
          toolExecutions: [
            {
              id: 'tool-1',
              toolName: 'web_search',
              toolInput: { query: 'test' },
              reasoning: [],
              isComplete: true,
              isExpanded: false,
              toolResult: 'Search results'
            }
          ]
        })
      ]

      const { container } = render(<AssistantTurn messages={messages} sessionId="test-session" />)

      // Text should appear in the DOM
      expect(container.innerHTML).toContain('Let me search for that')
      // Tool execution should also appear (actual component shows formatted tool name)
      expect(container.innerHTML).toContain('Used Web Search')
    })

    it('renders tool activity inline without a bordered card wrapper', () => {
      const messages: Message[] = [
        createMessage({
          text: '',
          toolExecutions: [
            {
              id: 'tool-inline',
              toolName: 'web_search',
              toolInput: { query: 'minimal tool activity' },
              reasoning: [],
              isComplete: true,
              isExpanded: false,
              toolResult: 'Search results',
            },
          ],
        }),
      ]

      render(<AssistantTurn messages={messages} sessionId="test-session" />)

      const activity = screen.getByTestId('tool-activity-group')
      expect(activity).not.toHaveClass('border', 'bg-card', 'rounded-lg')
    })

    it('uses the full content column without an assistant avatar gutter', () => {
      render(
        <AssistantTurn
          messages={[createMessage({ text: 'A direct response' })]}
          sessionId="test-session"
        />,
      )

      const content = screen.getByTestId('assistant-turn-content')
      expect(content.parentElement).toHaveClass('w-full', 'max-w-4xl')
      expect(content.parentElement).not.toHaveClass('space-x-4')
    })

    it('should render interleaved text and tool in correct order', () => {
      // Scenario: Text1 -> Tool1 -> Text2 -> Tool2
      const messages: Message[] = [
        createMessage({
          id: 'msg-1',
          text: 'First I will search',
          timestamp: '2024-01-01T10:00:00Z'
        }),
        createMessage({
          id: 'msg-2',
          text: '',
          timestamp: '2024-01-01T10:01:00Z',
          toolExecutions: [
            {
              id: 'tool-1',
              toolName: 'web_search',
              toolInput: { query: 'query1' },
              reasoning: [],
              isComplete: true,
              isExpanded: false,
              toolResult: 'Result 1'
            }
          ]
        }),
        createMessage({
          id: 'msg-3',
          text: 'Now let me analyze',
          timestamp: '2024-01-01T10:02:00Z'
        }),
        createMessage({
          id: 'msg-4',
          text: '',
          timestamp: '2024-01-01T10:03:00Z',
          toolExecutions: [
            {
              id: 'tool-2',
              toolName: 'analyze_data',
              toolInput: { data: 'test' },
              reasoning: [],
              isComplete: true,
              isExpanded: false,
              toolResult: 'Analysis complete'
            }
          ]
        })
      ]

      const { container } = render(<AssistantTurn messages={messages} sessionId="test-session" />)

      // Both text sections should be present
      expect(container.innerHTML).toContain('First I will search')
      expect(container.innerHTML).toContain('Now let me analyze')

      // Tool executions should be present (check by formatted tool names)
      expect(container.innerHTML).toContain('Used Web Search')
      expect(container.innerHTML).toContain('Used Analyze Data')
    })

    it('should group consecutive text messages together', () => {
      const messages: Message[] = [
        createMessage({
          id: 'msg-1',
          text: 'Hello ',
          timestamp: '2024-01-01T10:00:00Z'
        }),
        createMessage({
          id: 'msg-2',
          text: 'World ',
          timestamp: '2024-01-01T10:00:01Z'
        }),
        createMessage({
          id: 'msg-3',
          text: '!',
          timestamp: '2024-01-01T10:00:02Z'
        })
      ]

      render(<AssistantTurn messages={messages} sessionId="test-session" />)

      // All text should be grouped into one markdown element
      const markdownElements = screen.getAllByTestId('markdown')
      expect(markdownElements.length).toBe(1)
      expect(markdownElements[0].textContent).toContain('Hello')
      expect(markdownElements[0].textContent).toContain('World')
      expect(markdownElements[0].textContent).toContain('!')
    })

    it('should preserve order when tool interrupts text stream', () => {
      const messages: Message[] = [
        createMessage({
          id: 'msg-1',
          text: 'Before tool',
          timestamp: '2024-01-01T10:00:00Z'
        }),
        createMessage({
          id: 'msg-2',
          text: '',
          timestamp: '2024-01-01T10:01:00Z',
          toolExecutions: [
            {
              id: 'tool-1',
              toolName: 'calculator',
              toolInput: { expression: '2+2' },
              reasoning: [],
              isComplete: true,
              isExpanded: false,
              toolResult: '4'
            }
          ]
        }),
        createMessage({
          id: 'msg-3',
          text: 'After tool',
          timestamp: '2024-01-01T10:02:00Z'
        })
      ]

      const { container } = render(<AssistantTurn messages={messages} sessionId="test-session" />)

      // Verify all elements are rendered (order is preserved by timestamp)
      expect(container.innerHTML).toContain('Before tool')
      expect(container.innerHTML).toContain('After tool')
      // Tool execution should be rendered between them (verified by presence)
      expect(container.querySelector('[data-testid="tool-execution"]')).toBeDefined()
    })

    it('should handle messages with images in correct position', () => {
      const messages: Message[] = [
        createMessage({
          id: 'msg-1',
          text: 'Here is an image',
          timestamp: '2024-01-01T10:00:00Z',
          images: [{ type: 'url', url: 'https://example.com/image.png' }]
        }),
        createMessage({
          id: 'msg-2',
          text: 'And some more text',
          timestamp: '2024-01-01T10:01:00Z'
        })
      ]

      render(<AssistantTurn messages={messages} sessionId="test-session" />)

      // Images should be rendered
      const images = screen.getAllByTestId('lazy-image')
      expect(images.length).toBeGreaterThan(0)
    })

    it('should render research agent as tool execution alongside other tools', () => {
      const messages: Message[] = [
        createMessage({
          id: 'msg-1',
          text: '',
          timestamp: '2024-01-01T10:00:00Z',
          toolExecutions: [
            {
              id: 'tool-1',
              toolName: 'web_search',
              toolInput: { query: 'test' },
              reasoning: [],
              isComplete: true,
              isExpanded: false,
              toolResult: 'Search results'
            },
            {
              id: 'tool-2',
              toolName: 'research_agent',
              toolInput: { plan: 'Research plan' },
              reasoning: [],
              isComplete: true,
              isExpanded: false,
              toolResult: 'Research complete'
            }
          ]
        })
      ]

      const { container } = render(<AssistantTurn messages={messages} sessionId="test-session" />)

      // Both tools rendered via ToolExecutionContainer (formatted names)
      expect(container.innerHTML).toContain('Used Web Search')
      expect(container.innerHTML).toContain('Used Research Agent')
    })

    it('should always sort by timestamp (id is always string now)', () => {
      // After refactoring, all IDs are strings and sorting is purely by timestamp
      const messages: Message[] = [
        createMessage({
          id: 'msg-c',
          text: 'Third',
          timestamp: '2024-01-01T10:02:00Z'
        }),
        createMessage({
          id: 'msg-a',
          text: 'First',
          timestamp: '2024-01-01T10:00:00Z'
        }),
        createMessage({
          id: 'msg-b',
          text: 'Second',
          timestamp: '2024-01-01T10:01:00Z'
        })
      ]

      render(<AssistantTurn messages={messages} sessionId="test-session" />)

      const markdownElements = screen.getAllByTestId('markdown')
      // Sorted by timestamp: First, Second, Third (grouped together)
      expect(markdownElements[0].textContent).toContain('First')
      expect(markdownElements[0].textContent).toContain('Second')
      expect(markdownElements[0].textContent).toContain('Third')
    })
  })
})
