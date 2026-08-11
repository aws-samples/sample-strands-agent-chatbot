import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useChat } from '@/hooks/useChat'

const detachStream = vi.fn()
const resetStreamingState = vi.fn()

// Mock dependencies
vi.mock('@/utils/chat', () => ({
  detectBackendUrl: vi.fn().mockResolvedValue({ url: 'http://localhost:8000', connected: true }),
  getToolIconById: vi.fn(),
  getCategoryColor: vi.fn()
}))

vi.mock('@/hooks/useStreamEvents', () => ({
  useStreamEvents: vi.fn(() => ({
    handleStreamEvent: vi.fn(),
    resetStreamingState
  }))
}))

vi.mock('@/hooks/useChatAPI', () => ({
  useChatAPI: vi.fn(() => ({
    newChat: vi.fn().mockResolvedValue(true),
    sendMessage: vi.fn().mockResolvedValue(undefined),
    replayExecution: vi.fn().mockResolvedValue(true),
    detachStream,
    cleanup: vi.fn(),
    sendStopSignal: vi.fn(),
    loadSession: vi.fn().mockResolvedValue(null)
  }))
}))

vi.mock('@/config/environment', () => ({
  getApiUrl: vi.fn((path: string) => `http://localhost:8000/${path}`)
}))

vi.mock('@/lib/api-client', () => ({
  apiPost: vi.fn().mockResolvedValue({ success: true })
}))

vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: vi.fn().mockResolvedValue({ tokens: null })
}))

describe('useChat Hook', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe('Initial State', () => {
    it('should initialize with empty messages', async () => {
      const { result } = renderHook(() => useChat())

      // Wait for initial effects to settle
      await act(async () => {
        await vi.runAllTimersAsync()
      })

      expect(result.current.messages).toEqual([])
    })

    it('should initialize with idle agent status', async () => {
      const { result } = renderHook(() => useChat())

      await act(async () => {
        await vi.runAllTimersAsync()
      })

      expect(result.current.agentStatus).toBe('idle')
    })

    it('should initialize without typing indicator', async () => {
      const { result } = renderHook(() => useChat())

      await act(async () => {
        await vi.runAllTimersAsync()
      })

      expect(result.current.isTyping).toBe(false)
    })

    it('should initialize with a generated sessionId', async () => {
      const { result } = renderHook(() => useChat())

      await act(async () => {
        await vi.runAllTimersAsync()
      })

      expect(typeof result.current.sessionId).toBe('string')
      expect(result.current.sessionId.length).toBeGreaterThan(0)
    })
  })

  describe('Input Message', () => {
    it('should no longer expose inputMessage (moved to ChatInputArea local state)', async () => {
      const { result } = renderHook(() => useChat())

      await act(async () => {
        await vi.runAllTimersAsync()
      })

      // inputMessage is now local to ChatInputArea, not in useChat
      expect(result.current).not.toHaveProperty('inputMessage')
      expect(result.current).not.toHaveProperty('setInputMessage')
    })
  })

  describe('Backend Detection', () => {
    it('should detect backend URL on mount', async () => {
      const { detectBackendUrl } = await import('@/utils/chat')

      renderHook(() => useChat())

      await act(async () => {
        await vi.runAllTimersAsync()
      })

      expect(detectBackendUrl).toHaveBeenCalled()
    })

    it('should set connected state based on backend detection', async () => {
      const { result } = renderHook(() => useChat())

      await act(async () => {
        await vi.runAllTimersAsync()
      })

      // Since mock returns { connected: true }
      expect(result.current.isConnected).toBe(true)
    })
  })

  describe('Progress Panel', () => {
    it('should toggle progress panel visibility', async () => {
      const { result } = renderHook(() => useChat())

      await act(async () => {
        await vi.runAllTimersAsync()
      })

      expect(result.current.showProgressPanel).toBe(false)

      act(() => {
        result.current.toggleProgressPanel()
      })

      expect(result.current.showProgressPanel).toBe(true)

      act(() => {
        result.current.toggleProgressPanel()
      })

      expect(result.current.showProgressPanel).toBe(false)
    })
  })

  describe('Grouped Messages', () => {
    it('should return empty array when no messages', async () => {
      const { result } = renderHook(() => useChat())

      await act(async () => {
        await vi.runAllTimersAsync()
      })

      expect(result.current.groupedMessages).toEqual([])
    })
  })

  describe('Browser Session', () => {
    it('should initialize with null browser session', async () => {
      const { result } = renderHook(() => useChat())

      await act(async () => {
        await vi.runAllTimersAsync()
      })

      expect(result.current.browserSession).toBe(null)
    })
  })

  describe('Interrupt Handling', () => {
    it('should initialize with null current interrupt', async () => {
      const { result } = renderHook(() => useChat())

      await act(async () => {
        await vi.runAllTimersAsync()
      })

      expect(result.current.currentInterrupt).toBe(null)
    })
  })
})

describe('useChat Hook - State Management', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe('Current Tool Executions', () => {
    it('should initialize with empty tool executions', async () => {
      const { result } = renderHook(() => useChat())

      await act(async () => {
        await vi.runAllTimersAsync()
      })

      expect(result.current.currentToolExecutions).toEqual([])
    })
  })

  describe('Reasoning State', () => {
    it('should initialize with null reasoning state', async () => {
      const { result } = renderHook(() => useChat())

      await act(async () => {
        await vi.runAllTimersAsync()
      })

      expect(result.current.currentReasoning).toBe(null)
    })
  })
})
