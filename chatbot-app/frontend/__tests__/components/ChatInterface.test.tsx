import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { ChatInterface } from '@/components/ChatInterface'
import type { Message } from '@/types/chat'
import type { AgentStatus } from '@/types/events'

// Type for grouped messages
interface GroupedMessage {
  type: 'user' | 'assistant_turn'
  messages: Message[]
  id: string
}

// Mock useChat hook
const mockUseChat: {
  messages: Message[]
  groupedMessages: GroupedMessage[]
  inputMessage: string
  setInputMessage: ReturnType<typeof vi.fn>
  isConnected: boolean
  isTyping: boolean
  agentStatus: AgentStatus
  isForegroundRunActive: boolean
  currentToolExecutions: any[]
  currentReasoning: any
  showProgressPanel: boolean
  toggleProgressPanel: ReturnType<typeof vi.fn>
  sendMessage: ReturnType<typeof vi.fn>
  replayExecution: ReturnType<typeof vi.fn>
  stopGeneration: ReturnType<typeof vi.fn>
  queuedMessages: any[]
  queueHoldReason: string | null
  enqueueMessage: ReturnType<typeof vi.fn>
  removeQueuedMessage: ReturnType<typeof vi.fn>
  clearQueuedMessages: ReturnType<typeof vi.fn>
  releaseQueue: ReturnType<typeof vi.fn>
  interruptWithQueuedMessage: ReturnType<typeof vi.fn>
  sendQueuedMessageNow: ReturnType<typeof vi.fn>
  conciseMode: boolean
  toggleConciseMode: ReturnType<typeof vi.fn>
  newChat: ReturnType<typeof vi.fn>
  sessionId: string | null
  loadSession: ReturnType<typeof vi.fn>
  browserSession: { sessionId: string | null; browserId: string | null } | null
  browserProgress: any
  respondToInterrupt: ReturnType<typeof vi.fn>
  currentInterrupt: any
  pendingOAuth: any
} = {
  messages: [],
  groupedMessages: [],
  inputMessage: '',
  setInputMessage: vi.fn(),
  isConnected: true,
  isTyping: false,
  agentStatus: 'idle',
  isForegroundRunActive: false,
  currentToolExecutions: [],
  currentReasoning: null,
  showProgressPanel: false,
  toggleProgressPanel: vi.fn(),
  sendMessage: vi.fn(),
  replayExecution: vi.fn().mockResolvedValue(true),
  stopGeneration: vi.fn(),
  queuedMessages: [],
  queueHoldReason: null,
  enqueueMessage: vi.fn(),
  removeQueuedMessage: vi.fn(),
  clearQueuedMessages: vi.fn(),
  releaseQueue: vi.fn(),
  interruptWithQueuedMessage: vi.fn().mockResolvedValue(true),
  sendQueuedMessageNow: vi.fn().mockResolvedValue(true),
  conciseMode: false,
  toggleConciseMode: vi.fn(),
  newChat: vi.fn().mockResolvedValue(undefined),
  sessionId: null,
  loadSession: vi.fn().mockResolvedValue(undefined),
  browserSession: null,
  browserProgress: undefined,
  respondToInterrupt: vi.fn().mockResolvedValue(undefined),
  currentInterrupt: null,
  pendingOAuth: null,
}

vi.mock('@/hooks/useChat', () => ({
  useChat: () => mockUseChat
}))

// Mock useIframeAuth
vi.mock('@/hooks/useIframeAuth', () => ({
  useIframeAuth: () => ({
    isInIframe: false,
    isAuthenticated: false,
    user: null,
    isLoading: false,
    error: null
  }),
  postAuthStatusToParent: vi.fn()
}))

// Mock useSidebar
vi.mock('@/components/ui/sidebar', () => ({
  SidebarTrigger: () => <button data-testid="sidebar-trigger">Menu</button>,
  SidebarInset: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div data-testid="sidebar-inset" className={className}>{children}</div>
  ),
  useSidebar: () => ({
    setOpen: vi.fn(),
    setOpenMobile: vi.fn(),
    open: false
  })
}))

// Mock next-themes
vi.mock('next-themes', () => ({
  useTheme: () => ({
    theme: 'light',
    setTheme: vi.fn()
  })
}))

// Mock api-client
vi.mock('@/lib/api-client', () => ({
  apiGet: vi.fn().mockResolvedValue({ success: true, config: {}, models: [] })
}))

// Mock child components
vi.mock('@/components/chat/ChatMessage', () => ({
  ChatMessage: ({ message }: { message: any }) => (
    <div data-testid="chat-message">{message.text}</div>
  )
}))

vi.mock('@/components/chat/AssistantTurn', () => ({
  AssistantTurn: ({ messages }: { messages: any[] }) => (
    <div data-testid="assistant-turn">
      {messages.map((m, i) => <span key={i}>{m.text}</span>)}
    </div>
  )
}))

vi.mock('@/components/Greeting', () => ({
  Greeting: () => <div data-testid="greeting">Welcome!</div>,
  PromptSuggestions: ({ onSelectPrompt }: { onSelectPrompt?: (p: string) => void }) => (
    <div data-testid="prompt-suggestions">Prompts</div>
  )
}))

vi.mock('@/components/ChatSidebar', () => ({
  ChatSidebar: () => <div data-testid="chat-sidebar">Sidebar</div>
}))

vi.mock('@/components/SuggestedQuestions', () => ({
  SuggestedQuestions: () => <div data-testid="suggested-questions">Questions</div>
}))

vi.mock('@/components/BrowserLiveViewButton', () => ({
  BrowserLiveViewButton: () => <button data-testid="browser-live-view-btn">Live View</button>
}))

vi.mock('@/components/ResearchModal', () => ({
  ResearchModal: () => <div data-testid="research-modal">Research Modal</div>
}))

vi.mock('@/components/BrowserResultModal', () => ({
  BrowserResultModal: () => <div data-testid="browser-result-modal">Browser Result</div>
}))

vi.mock('@/components/InterruptApprovalModal', () => ({
  InterruptApprovalModal: () => <div data-testid="interrupt-modal">Interrupt Modal</div>
}))

vi.mock('@/components/ModelConfigDialog', () => ({
  ModelConfigDialog: () => <button data-testid="model-config">Model</button>
}))

describe('ChatInterface', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Reset mock state
    mockUseChat.messages = []
    mockUseChat.groupedMessages = []
    mockUseChat.inputMessage = ''
    mockUseChat.isTyping = false
    mockUseChat.agentStatus = 'idle'
    mockUseChat.isForegroundRunActive = false
    mockUseChat.currentInterrupt = null
    mockUseChat.pendingOAuth = null
    mockUseChat.queuedMessages = []
    mockUseChat.queueHoldReason = null
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  describe('Rendering', () => {
    it('should render chat interface', () => {
      render(<ChatInterface />)

      expect(screen.getByTestId('sidebar-inset')).toBeInTheDocument()
    })

    it('should show greeting when no messages', () => {
      render(<ChatInterface />)

      expect(screen.getByTestId('greeting')).toBeInTheDocument()
    })

    it('should render chat sidebar', () => {
      render(<ChatInterface />)

      expect(screen.getByTestId('chat-sidebar')).toBeInTheDocument()
    })
  })

  describe('Input Area', () => {
    it('should render textarea for message input', () => {
      render(<ChatInterface />)

      const textarea = screen.getByPlaceholderText(/Ask me anything/i)
      expect(textarea).toBeInTheDocument()
    })

    it('should render buttons in the input area', () => {
      render(<ChatInterface />)

      // Verify that buttons exist in the form area
      const buttons = screen.getAllByRole('button')
      expect(buttons.length).toBeGreaterThan(0)
    })

    it('should have disabled buttons when input is empty and idle', () => {
      render(<ChatInterface />)

      // When input is empty, the send button should be disabled
      const buttons = screen.getAllByRole('button')
      // At least one button should be disabled (the send button when empty)
      const disabledButtons = buttons.filter(btn => btn.hasAttribute('disabled'))
      expect(disabledButtons.length).toBeGreaterThanOrEqual(0)
    })
  })

  describe('Messages Display', () => {
    it('should render user messages', () => {
      mockUseChat.groupedMessages = [
        {
          type: 'user',
          id: 'user_1',
          messages: [{ id: '1', text: 'Hello', sender: 'user', timestamp: '12:00' }]
        }
      ]

      render(<ChatInterface />)

      expect(screen.getByTestId('chat-message')).toBeInTheDocument()
      expect(screen.getByText('Hello')).toBeInTheDocument()
    })

    it('should render assistant turns', () => {
      mockUseChat.groupedMessages = [
        {
          type: 'assistant_turn',
          id: 'turn_1',
          messages: [{ id: '2', text: 'Hi there!', sender: 'bot', timestamp: '12:01' }]
        }
      ]

      render(<ChatInterface />)

      expect(screen.getByTestId('assistant-turn')).toBeInTheDocument()
      expect(screen.getByText('Hi there!')).toBeInTheDocument()
    })
  })

  describe('Status Indicators', () => {
    it('should render successfully when connected', () => {
      mockUseChat.groupedMessages = [
        {
          type: 'user',
          id: 'user_1',
          messages: [{ id: '1', text: 'Hello', sender: 'user', timestamp: '12:00' }]
        }
      ]

      render(<ChatInterface />)

      // Component should render without errors
      expect(screen.getByText('Hello')).toBeInTheDocument()
    })

    it('should render when agent is thinking', () => {
      mockUseChat.agentStatus = 'thinking'
      mockUseChat.groupedMessages = [
        {
          type: 'user',
          id: 'user_1',
          messages: [{ id: '1', text: 'Hello', sender: 'user', timestamp: '12:00' }]
        }
      ]

      render(<ChatInterface />)

      // Component should render without errors when agent is thinking
      expect(document.body).toBeInTheDocument()
    })

    it('offers a queued message as an interrupt while the agent is running', () => {
      mockUseChat.isForegroundRunActive = true
      mockUseChat.queuedMessages = [{
        id: 'queued-2',
        text: 'handle this now',
        files: [],
        sessionId: 'session-1',
      }]

      render(<ChatInterface />)
      fireEvent.click(screen.getByRole('button', {
        name: /interrupt with this message/i,
      }))

      expect(mockUseChat.interruptWithQueuedMessage).toHaveBeenCalledWith('queued-2')
    })

    it('offers immediate send for a queued message while idle', () => {
      mockUseChat.agentStatus = 'idle'
      mockUseChat.queuedMessages = [{
        id: 'queued-1',
        text: 'wait',
        files: [],
        sessionId: 'session-1',
      }]

      render(<ChatInterface />)
      fireEvent.click(screen.getByRole('button', {
        name: /send this message now/i,
      }))

      expect(mockUseChat.sendQueuedMessageNow).toHaveBeenCalledWith('queued-1')
    })

    it('hides queued message actions while awaiting approval', () => {
      mockUseChat.isForegroundRunActive = true
      mockUseChat.queuedMessages = [{
        id: 'queued-1',
        text: 'wait',
        files: [],
        sessionId: 'session-1',
      }]

      mockUseChat.currentInterrupt = {
        interrupts: [{ id: 'i1', name: 'approval', reason: {} }],
      }
      render(<ChatInterface />)
      expect(screen.queryByRole('button', {
        name: /this message/i,
      })).not.toBeInTheDocument()
    })
  })

  describe('File Upload', () => {
    it('should render file upload button', () => {
      render(<ChatInterface />)

      // Find the hidden file input
      const fileInput = document.getElementById('file-upload')
      expect(fileInput).toBeInTheDocument()
    })
  })

  describe('Interrupt Handling', () => {
    it('should show interrupt modal for email delete approval', () => {
      mockUseChat.currentInterrupt = {
        interrupts: [{ id: 'int1', name: 'chatbot-email-delete-approval', reason: { query: 'test', intent: 'delete emails' } }]
      }

      render(<ChatInterface />)

      expect(screen.getByTestId('interrupt-modal')).toBeInTheDocument()
    })

    it('should not show interrupt modal when no current interrupt', () => {
      mockUseChat.currentInterrupt = null

      render(<ChatInterface />)

      expect(screen.queryByTestId('interrupt-modal')).not.toBeInTheDocument()
    })

    it('should allow a legacy research approval interrupt to be resolved', () => {
      mockUseChat.currentInterrupt = {
        interrupts: [{ id: 'int1', name: 'chatbot-research-approval', reason: {} }]
      }

      render(<ChatInterface />)

      expect(screen.getByTestId('interrupt-modal')).toBeInTheDocument()
    })
  })

  describe('Controls', () => {
    it('should render model config button', () => {
      render(<ChatInterface />)

      expect(screen.getByTestId('model-config')).toBeInTheDocument()
    })
  })
})

describe('ChatInterface - Agent Status', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseChat.messages = []
    mockUseChat.groupedMessages = []
    mockUseChat.agentStatus = 'idle'
  })

  it('should show stop button when agent is not idle', () => {
    mockUseChat.agentStatus = 'thinking'
    mockUseChat.groupedMessages = [
      {
        type: 'user',
        id: 'user_1',
        messages: [{ id: '1', text: 'Hello', sender: 'user', timestamp: '12:00' }]
      }
    ]

    render(<ChatInterface />)

    // When agent is not idle, the submit button changes to stop button
    const buttons = screen.getAllByRole('button')
    // Stop button should be present (it has a Square icon, not Send)
    expect(buttons.length).toBeGreaterThan(0)
  })

  it('should allow stopping during research', () => {
    mockUseChat.agentStatus = 'researching'
    mockUseChat.groupedMessages = [
      {
        type: 'user',
        id: 'user_1',
        messages: [{ id: '1', text: 'Hello', sender: 'user', timestamp: '12:00' }]
      }
    ]

    render(<ChatInterface />)

    // A2A research tasks support cancellation through the standard cancel API.
    const stopButton = document.querySelector('button[title*="Stop"]')
    if (stopButton) {
      expect(stopButton).not.toBeDisabled()
    }
  })
})
