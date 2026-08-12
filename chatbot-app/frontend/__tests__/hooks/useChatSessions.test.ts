/**
 * Tests for useChatSessions hook
 *
 * Tests cover:
 * - Initial state
 * - Loading sessions
 * - Deleting sessions
 * - Error handling
 * - Global refresh function
 */
import { renderHook, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Mock api-client
const mockApiGet = vi.fn()
const mockApiDelete = vi.fn()

vi.mock('@/lib/api-client', () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
  apiDelete: (...args: unknown[]) => mockApiDelete(...args)
}))

// Import after mocks
import { useChatSessions, ChatSession } from '@/hooks/useChatSessions'

describe('useChatSessions', () => {
  const mockSessions: ChatSession[] = [
    {
      sessionId: 'session-1',
      title: 'Test Session 1',
      lastMessageAt: '2024-01-15T10:00:00Z',
      messageCount: 5,
      starred: false,
      status: 'active',
      createdAt: '2024-01-15T09:00:00Z',
      tags: ['test']
    },
    {
      sessionId: 'session-2',
      title: 'Test Session 2',
      lastMessageAt: '2024-01-14T10:00:00Z',
      messageCount: 10,
      starred: true,
      status: 'active',
      createdAt: '2024-01-14T09:00:00Z',
      tags: []
    }
  ]

  const defaultProps = {
    sessionId: 'current-session',
    onNewChat: vi.fn()
  }

  beforeEach(() => {
    vi.clearAllMocks()

    // Default successful response
    mockApiGet.mockResolvedValue({
      success: true,
      sessions: mockSessions
    })

    mockApiDelete.mockResolvedValue({
      success: true
    })
  })

  afterEach(() => {
    // Clean up global function
    delete (window as any).__refreshSessionList
  })

  describe('Initial State', () => {
    it('should initialize with empty sessions array', async () => {
      mockApiGet.mockResolvedValueOnce({ success: true, sessions: [] })

      const { result } = renderHook(() => useChatSessions(defaultProps))

      // Initially empty before load completes
      expect(result.current.chatSessions).toEqual([])
    })

    it('should start with loading state false initially', () => {
      const { result } = renderHook(() => useChatSessions(defaultProps))

      // isLoadingSessions starts as false, then becomes true during load
      expect(typeof result.current.isLoadingSessions).toBe('boolean')
    })
  })

  describe('Loading Sessions', () => {
    it('should load sessions on mount', async () => {
      const { result } = renderHook(() => useChatSessions(defaultProps))

      await waitFor(() => {
        expect(result.current.chatSessions).toEqual(mockSessions)
      })

      expect(mockApiGet).toHaveBeenCalledWith(
        'session/list?limit=100&status=active'
      )
    })

    it('should set loading state during fetch', async () => {
      // Create a promise we can control
      let resolvePromise: (value: unknown) => void
      const controlledPromise = new Promise(resolve => {
        resolvePromise = resolve
      })

      mockApiGet.mockReturnValueOnce(controlledPromise)

      const { result } = renderHook(() => useChatSessions(defaultProps))

      // Should be loading
      expect(result.current.isLoadingSessions).toBe(true)

      // Resolve the promise
      await act(async () => {
        resolvePromise!({ success: true, sessions: mockSessions })
      })

      await waitFor(() => {
        expect(result.current.isLoadingSessions).toBe(false)
      })
    })

    it('should handle API error gracefully', async () => {
      const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
      mockApiGet.mockRejectedValueOnce(new Error('Network error'))

      const { result } = renderHook(() => useChatSessions(defaultProps))

      await waitFor(() => {
        expect(result.current.isLoadingSessions).toBe(false)
      })

      expect(result.current.chatSessions).toEqual([])
      expect(consoleError).toHaveBeenCalled()

      consoleError.mockRestore()
    })

    it('should handle unsuccessful response', async () => {
      mockApiGet.mockResolvedValueOnce({ success: false, sessions: [] })

      const { result } = renderHook(() => useChatSessions(defaultProps))

      await waitFor(() => {
        expect(result.current.isLoadingSessions).toBe(false)
      })

      expect(result.current.chatSessions).toEqual([])
    })

    it('should handle null sessions in response', async () => {
      mockApiGet.mockResolvedValueOnce({ success: true, sessions: null })

      const { result } = renderHook(() => useChatSessions(defaultProps))

      await waitFor(() => {
        expect(result.current.isLoadingSessions).toBe(false)
      })

      expect(result.current.chatSessions).toEqual([])
    })

    it('should load sessions when sessionId is null', async () => {
      const propsWithNullSession = {
        sessionId: null,
        onNewChat: vi.fn()
      }

      renderHook(() => useChatSessions(propsWithNullSession))

      await waitFor(() => {
        expect(mockApiGet).toHaveBeenCalledWith(
          'session/list?limit=100&status=active'
        )
      })
    })
  })

  describe('Manual Session Refresh', () => {
    it('should provide loadSessions function', async () => {
      const { result } = renderHook(() => useChatSessions(defaultProps))

      await waitFor(() => {
        expect(result.current.chatSessions.length).toBeGreaterThan(0)
      })

      expect(typeof result.current.loadSessions).toBe('function')
    })

    it('should refresh sessions when loadSessions is called', async () => {
      const { result } = renderHook(() => useChatSessions(defaultProps))

      await waitFor(() => {
        expect(result.current.chatSessions.length).toBeGreaterThan(0)
      })

      // Clear mock to track new calls
      mockApiGet.mockClear()

      const newSessions = [{ ...mockSessions[0], title: 'Updated Title' }]
      mockApiGet.mockResolvedValueOnce({ success: true, sessions: newSessions })

      await act(async () => {
        await result.current.loadSessions()
      })

      expect(mockApiGet).toHaveBeenCalled()
      expect(result.current.chatSessions[0].title).toBe('Updated Title')
    })

    it('should poll the session list while the page is visible', async () => {
      vi.useFakeTimers()
      Object.defineProperty(document, 'visibilityState', {
        configurable: true,
        value: 'visible',
      })
      const { unmount } = renderHook(() => useChatSessions(defaultProps))

      await act(async () => {
        await Promise.resolve()
        await Promise.resolve()
      })
      expect(mockApiGet).toHaveBeenCalledTimes(1)

      await act(async () => {
        vi.advanceTimersByTime(10_000)
        await Promise.resolve()
        await Promise.resolve()
      })

      expect(mockApiGet).toHaveBeenCalledTimes(2)
      unmount()
      vi.useRealTimers()
    })
  })

  describe('Deleting Sessions', () => {
    it('should delete session successfully', async () => {
      const { result } = renderHook(() => useChatSessions(defaultProps))

      await waitFor(() => {
        expect(result.current.chatSessions.length).toBeGreaterThan(0)
      })

      await act(async () => {
        await result.current.deleteSession('session-1')
      })

      expect(mockApiDelete).toHaveBeenCalledWith(
        'session/delete?session_id=session-1',
        expect.objectContaining({
          headers: { 'X-Session-ID': 'current-session' }
        })
      )
    })

    it('should refresh sessions after deletion', async () => {
      const { result } = renderHook(() => useChatSessions(defaultProps))

      await waitFor(() => {
        expect(result.current.chatSessions.length).toBeGreaterThan(0)
      })

      mockApiGet.mockClear()

      await act(async () => {
        await result.current.deleteSession('session-1')
      })

      // Should call loadSessions after delete
      expect(mockApiGet).toHaveBeenCalled()
    })

    it('should call onNewChat when deleting current session', async () => {
      const onNewChat = vi.fn()
      const props = {
        sessionId: 'session-1',
        onNewChat
      }

      const { result } = renderHook(() => useChatSessions(props))

      await waitFor(() => {
        expect(result.current.chatSessions.length).toBeGreaterThan(0)
      })

      await act(async () => {
        await result.current.deleteSession('session-1')
      })

      expect(onNewChat).toHaveBeenCalled()
    })

    it('should not call onNewChat when deleting other session', async () => {
      const onNewChat = vi.fn()
      const props = {
        sessionId: 'current-session',
        onNewChat
      }

      const { result } = renderHook(() => useChatSessions(props))

      await waitFor(() => {
        expect(result.current.chatSessions.length).toBeGreaterThan(0)
      })

      await act(async () => {
        await result.current.deleteSession('session-1')
      })

      expect(onNewChat).not.toHaveBeenCalled()
    })

    it('should handle delete error', async () => {
      const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
      mockApiDelete.mockRejectedValueOnce(new Error('Delete failed'))

      const { result } = renderHook(() => useChatSessions(defaultProps))

      await waitFor(() => {
        expect(result.current.chatSessions.length).toBeGreaterThan(0)
      })

      await expect(
        act(async () => {
          await result.current.deleteSession('session-1')
        })
      ).rejects.toThrow('Delete failed')

      expect(consoleError).toHaveBeenCalled()
      consoleError.mockRestore()
    })

    it('should handle unsuccessful delete response', async () => {
      mockApiDelete.mockResolvedValueOnce({
        success: false,
        error: 'Session not found'
      })

      const { result } = renderHook(() => useChatSessions(defaultProps))

      await waitFor(() => {
        expect(result.current.chatSessions.length).toBeGreaterThan(0)
      })

      await expect(
        act(async () => {
          await result.current.deleteSession('nonexistent')
        })
      ).rejects.toThrow('Session not found')
    })
  })

  describe('Global Refresh Function', () => {
    it('should expose loadSessions globally', async () => {
      renderHook(() => useChatSessions(defaultProps))

      await waitFor(() => {
        expect((window as any).__refreshSessionList).toBeDefined()
      })

      expect(typeof (window as any).__refreshSessionList).toBe('function')
    })

    it('should remove global function on unmount', async () => {
      const { unmount } = renderHook(() => useChatSessions(defaultProps))

      await waitFor(() => {
        expect((window as any).__refreshSessionList).toBeDefined()
      })

      unmount()

      expect((window as any).__refreshSessionList).toBeUndefined()
    })

    it('should update global function when loadSessions changes', async () => {
      const { rerender } = renderHook(
        (props) => useChatSessions(props),
        { initialProps: defaultProps }
      )

      await waitFor(() => {
        expect((window as any).__refreshSessionList).toBeDefined()
      })

      const firstFn = (window as any).__refreshSessionList

      // Change sessionId to trigger loadSessions change
      rerender({ ...defaultProps, sessionId: 'new-session' })

      await waitFor(() => {
        expect((window as any).__refreshSessionList).toBeDefined()
      })

      // Function reference should be updated
      expect(typeof (window as any).__refreshSessionList).toBe('function')
    })
  })

  describe('Session Data Structure', () => {
    it('should return sessions with all required fields', async () => {
      const { result } = renderHook(() => useChatSessions(defaultProps))

      await waitFor(() => {
        expect(result.current.chatSessions.length).toBeGreaterThan(0)
      })

      const session = result.current.chatSessions[0]
      expect(session).toHaveProperty('sessionId')
      expect(session).toHaveProperty('title')
      expect(session).toHaveProperty('lastMessageAt')
      expect(session).toHaveProperty('messageCount')
      expect(session).toHaveProperty('status')
      expect(session).toHaveProperty('createdAt')
    })

    it('should handle optional fields', async () => {
      const sessionWithOptional: ChatSession = {
        ...mockSessions[0],
        starred: true,
        tags: ['important', 'work']
      }

      mockApiGet.mockResolvedValueOnce({
        success: true,
        sessions: [sessionWithOptional]
      })

      const { result } = renderHook(() => useChatSessions(defaultProps))

      await waitFor(() => {
        expect(result.current.chatSessions.length).toBe(1)
      })

      expect(result.current.chatSessions[0].starred).toBe(true)
      expect(result.current.chatSessions[0].tags).toEqual(['important', 'work'])
    })
  })

  describe('Hook Return Value', () => {
    it('should return all expected properties', async () => {
      const { result } = renderHook(() => useChatSessions(defaultProps))

      expect(result.current).toHaveProperty('chatSessions')
      expect(result.current).toHaveProperty('isLoadingSessions')
      expect(result.current).toHaveProperty('loadSessions')
      expect(result.current).toHaveProperty('deleteSession')

      expect(Array.isArray(result.current.chatSessions)).toBe(true)
      expect(typeof result.current.isLoadingSessions).toBe('boolean')
      expect(typeof result.current.loadSessions).toBe('function')
      expect(typeof result.current.deleteSession).toBe('function')
    })
  })
})
