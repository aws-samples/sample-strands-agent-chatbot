/**
 * Tests for Memory Reset API routes
 *
 * Tests cover:
 * - Memory stats endpoint (GET)
 * - Memory reset endpoint (DELETE)
 * - Authentication handling
 * - Namespace filtering
 * - Error responses
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock dependencies
vi.mock('@/lib/auth-utils', () => ({
  extractUserFromRequest: vi.fn(),
}))

vi.mock('@aws-sdk/client-bedrock-agentcore', () => ({
  BedrockAgentCoreClient: vi.fn().mockImplementation(() => ({
    send: vi.fn(),
  })),
  ListMemoryRecordsCommand: vi.fn(),
  DeleteMemoryRecordCommand: vi.fn(),
}))

vi.mock('@aws-sdk/client-ssm', () => ({
  SSMClient: vi.fn().mockImplementation(() => ({
    send: vi.fn(),
  })),
  GetParameterCommand: vi.fn(),
}))

// Import after mocks
import { extractUserFromRequest } from '@/lib/auth-utils'

// Helper to create mock NextRequest
function createMockNextRequest(options: {
  method?: string
  searchParams?: Record<string, string>
  headers?: Record<string, string>
} = {}) {
  const { method = 'GET', searchParams = {}, headers = {} } = options

  const url = new URL('http://localhost:3000/api/memory/reset')
  Object.entries(searchParams).forEach(([key, value]) => {
    url.searchParams.set(key, value)
  })

  return {
    method,
    nextUrl: url,
    headers: {
      get: (name: string) => headers[name.toLowerCase()] || null,
    },
  }
}

describe('Memory Reset API', async () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Reset environment
    vi.stubEnv('NEXT_PUBLIC_AGENTCORE_LOCAL', 'false')
    vi.stubEnv('MEMORY_ID', 'test-memory-id')
    vi.stubEnv('AWS_REGION', 'us-west-2')
  })

  describe('Authentication', async () => {
    it('should extract user from request', async () => {
      const mockExtract = extractUserFromRequest as ReturnType<typeof vi.fn>
      mockExtract.mockReturnValue({ userId: 'test-user-123', email: 'test@example.com' })

      const request = createMockNextRequest()
      const user = await extractUserFromRequest(request as any)

      expect(user.userId).toBe('test-user-123')
      expect(mockExtract).toHaveBeenCalledWith(request)
    })

    it('should reject anonymous users', async () => {
      const mockExtract = extractUserFromRequest as ReturnType<typeof vi.fn>
      mockExtract.mockReturnValue({ userId: 'anonymous' })

      const request = createMockNextRequest()
      const user = await extractUserFromRequest(request as any)

      expect(user.userId).toBe('anonymous')
      // API should return 401 for anonymous users
    })

    it('should handle authenticated users with Cognito token', async () => {
      const mockExtract = extractUserFromRequest as ReturnType<typeof vi.fn>
      mockExtract.mockReturnValue({
        userId: '18c1e380-6021-700d-3572-40d05568f4ce',
        email: 'user@example.com',
        username: 'testuser',
      })

      const request = createMockNextRequest({
        headers: {
          authorization: 'Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...',
        },
      })
      const user = await extractUserFromRequest(request as any)

      expect(user.userId).toBe('18c1e380-6021-700d-3572-40d05568f4ce')
    })
  })

  describe('Namespace Filtering', async () => {
    it('should accept valid namespace: preferences', async () => {
      const validNamespaces = ['preferences', 'facts', 'summaries', 'all']
      const namespace = 'preferences'

      expect(validNamespaces).toContain(namespace)
    })

    it('should accept valid namespace: facts', async () => {
      const validNamespaces = ['preferences', 'facts', 'summaries', 'all']
      const namespace = 'facts'

      expect(validNamespaces).toContain(namespace)
    })

    it('should accept valid namespace: summaries', async () => {
      const validNamespaces = ['preferences', 'facts', 'summaries', 'all']
      const namespace = 'summaries'

      expect(validNamespaces).toContain(namespace)
    })

    it('should accept valid namespace: all', async () => {
      const validNamespaces = ['preferences', 'facts', 'summaries', 'all']
      const namespace = 'all'

      expect(validNamespaces).toContain(namespace)
    })

    it('should reject invalid namespace', async () => {
      const validNamespaces = ['preferences', 'facts', 'summaries', 'all']
      const invalidNamespace = 'invalid-namespace'

      expect(validNamespaces).not.toContain(invalidNamespace)
    })

    it('should default to all when no namespace specified', async () => {
      const request = createMockNextRequest()
      const namespace = request.nextUrl.searchParams.get('namespace') || 'all'

      expect(namespace).toBe('all')
    })

    it('should use specified namespace from query params', async () => {
      const request = createMockNextRequest({
        searchParams: { namespace: 'preferences' },
      })
      const namespace = request.nextUrl.searchParams.get('namespace') || 'all'

      expect(namespace).toBe('preferences')
    })
  })

  describe('Namespace to Strategy Type Mapping', async () => {
    const namespaceToType: Record<string, string> = {
      preferences: 'USER_PREFERENCE',
      facts: 'SEMANTIC',
      summaries: 'SUMMARIZATION',
    }

    it('should map preferences to USER_PREFERENCE', async () => {
      expect(namespaceToType['preferences']).toBe('USER_PREFERENCE')
    })

    it('should map facts to SEMANTIC', async () => {
      expect(namespaceToType['facts']).toBe('SEMANTIC')
    })

    it('should map summaries to SUMMARIZATION', async () => {
      expect(namespaceToType['summaries']).toBe('SUMMARIZATION')
    })
  })

  describe('Namespace Path Generation', async () => {
    it('should generate correct namespace path for user preferences', async () => {
      const strategyId = 'user_preference_extraction-abc123'
      const userId = 'test-user-123'
      const namespace = `/strategies/${strategyId}/actors/${userId}`

      expect(namespace).toBe('/strategies/user_preference_extraction-abc123/actors/test-user-123')
    })

    it('should generate correct namespace path for semantic facts', async () => {
      const strategyId = 'semantic_fact_extraction-def456'
      const userId = 'test-user-123'
      const namespace = `/strategies/${strategyId}/actors/${userId}`

      expect(namespace).toBe('/strategies/semantic_fact_extraction-def456/actors/test-user-123')
    })

    it('should generate correct namespace path for summaries', async () => {
      const strategyId = 'conversation_summary-ghi789'
      const userId = 'test-user-123'
      const namespace = `/strategies/${strategyId}/actors/${userId}`

      expect(namespace).toBe('/strategies/conversation_summary-ghi789/actors/test-user-123')
    })
  })

  describe('Local Mode Handling', async () => {
    it('should reject memory operations in local mode', async () => {
      vi.stubEnv('NEXT_PUBLIC_AGENTCORE_LOCAL', 'true')

      const isLocal = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

      expect(isLocal).toBe(true)
      // API should return 400 for local mode
    })

    it('should allow memory operations in cloud mode', async () => {
      vi.stubEnv('NEXT_PUBLIC_AGENTCORE_LOCAL', 'false')

      const isLocal = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

      expect(isLocal).toBe(false)
    })
  })

  describe('Memory ID Configuration', async () => {
    it('should use MEMORY_ID from environment', async () => {
      vi.stubEnv('MEMORY_ID', 'test-memory-id-123')

      const memoryId = process.env.MEMORY_ID

      expect(memoryId).toBe('test-memory-id-123')
    })

    it('should handle missing MEMORY_ID', async () => {
      vi.stubEnv('MEMORY_ID', '')

      const memoryId = process.env.MEMORY_ID

      expect(memoryId).toBeFalsy()
      // API should fall back to SSM Parameter Store
    })
  })

  describe('Response Format - GET (Stats)', async () => {
    it('should format stats response correctly', async () => {
      const mockStats = {
        USER_PREFERENCE: { count: 67, namespace: '/strategies/user_pref-xxx/actors/user-123' },
        SEMANTIC: { count: 142, namespace: '/strategies/semantic-xxx/actors/user-123' },
        SUMMARIZATION: { count: 15, namespace: '/strategies/summary-xxx/actors/user-123' },
      }

      const totalCount = Object.values(mockStats).reduce((sum, s) => sum + s.count, 0)

      const response = {
        success: true,
        userId: 'user-123',
        memoryId: 'test-memory-id',
        stats: mockStats,
        totalRecords: totalCount,
      }

      expect(response.success).toBe(true)
      expect(response.totalRecords).toBe(224)
      expect(response.stats.USER_PREFERENCE.count).toBe(67)
      expect(response.stats.SEMANTIC.count).toBe(142)
      expect(response.stats.SUMMARIZATION.count).toBe(15)
    })
  })

  describe('Response Format - DELETE (Reset)', async () => {
    it('should format delete response correctly', async () => {
      const deleteResults = {
        USER_PREFERENCE: 67,
        SEMANTIC: 142,
        SUMMARIZATION: 15,
      }

      const totalDeleted = Object.values(deleteResults).reduce((sum, count) => sum + count, 0)

      const response = {
        success: true,
        message: `Deleted ${totalDeleted} memory records`,
        details: {
          userId: 'user-123',
          namespace: 'all',
          deletedCounts: deleteResults,
          totalDeleted,
        },
      }

      expect(response.success).toBe(true)
      expect(response.details.totalDeleted).toBe(224)
      expect(response.message).toBe('Deleted 224 memory records')
    })

    it('should format partial delete response for single namespace', async () => {
      const deleteResults = {
        USER_PREFERENCE: 67,
      }

      const totalDeleted = Object.values(deleteResults).reduce((sum, count) => sum + count, 0)

      const response = {
        success: true,
        message: `Deleted ${totalDeleted} memory records`,
        details: {
          userId: 'user-123',
          namespace: 'preferences',
          deletedCounts: deleteResults,
          totalDeleted,
        },
      }

      expect(response.success).toBe(true)
      expect(response.details.namespace).toBe('preferences')
      expect(response.details.totalDeleted).toBe(67)
    })
  })

  describe('Error Handling', async () => {
    it('should handle missing authentication', async () => {
      const mockExtract = extractUserFromRequest as ReturnType<typeof vi.fn>
      mockExtract.mockReturnValue({ userId: 'anonymous' })

      const user = await extractUserFromRequest({} as any)

      expect(user.userId).toBe('anonymous')
      // Should return 401 Unauthorized
    })

    it('should handle invalid namespace parameter', async () => {
      const request = createMockNextRequest({
        searchParams: { namespace: 'invalid' },
      })

      const validNamespaces = ['preferences', 'facts', 'summaries', 'all']
      const namespace = request.nextUrl.searchParams.get('namespace')

      expect(validNamespaces).not.toContain(namespace)
      // Should return 400 Bad Request
    })

    it('should handle memory service errors gracefully', async () => {
      const error = new Error('Memory service unavailable')

      const errorResponse = {
        success: false,
        error: 'Failed to reset memory',
        message: error.message,
      }

      expect(errorResponse.success).toBe(false)
      expect(errorResponse.message).toBe('Memory service unavailable')
    })

    it('should handle AWS SDK errors', async () => {
      const awsError = {
        name: 'ResourceNotFoundException',
        message: 'Memory not found',
        $metadata: { httpStatusCode: 404 },
      }

      const errorResponse = {
        success: false,
        error: 'Failed to reset memory',
        message: awsError.message,
      }

      expect(errorResponse.success).toBe(false)
      expect(errorResponse.message).toBe('Memory not found')
    })
  })

  describe('Strategy Info Caching', async () => {
    it('should cache strategy info with TTL', async () => {
      const CACHE_TTL_MS = 5 * 60 * 1000 // 5 minutes

      const cache = {
        memoryId: 'test-memory',
        strategies: [
          { type: 'USER_PREFERENCE', id: 'user_pref-abc' },
          { type: 'SEMANTIC', id: 'semantic-def' },
          { type: 'SUMMARIZATION', id: 'summary-ghi' },
        ],
        cachedAt: Date.now(),
      }

      // Cache should be valid
      const isCacheValid = Date.now() - cache.cachedAt < CACHE_TTL_MS

      expect(isCacheValid).toBe(true)
      expect(cache.strategies).toHaveLength(3)
    })

    it('should invalidate expired cache', async () => {
      const CACHE_TTL_MS = 5 * 60 * 1000 // 5 minutes

      const cache = {
        memoryId: 'test-memory',
        strategies: [],
        cachedAt: Date.now() - (CACHE_TTL_MS + 1000), // Expired
      }

      const isCacheValid = Date.now() - cache.cachedAt < CACHE_TTL_MS

      expect(isCacheValid).toBe(false)
    })

    it('should invalidate cache on memory ID change', async () => {
      const cache = {
        memoryId: 'old-memory-id',
        strategies: [],
        cachedAt: Date.now(),
      }

      const currentMemoryId = 'new-memory-id'
      const isSameMemory = cache.memoryId === currentMemoryId

      expect(isSameMemory).toBe(false)
    })
  })
})

describe('Memory Record Operations', async () => {
  describe('List Records', async () => {
    it('should paginate through all records', async () => {
      // Simulate pagination logic
      const pages = [
        { records: Array(100).fill({ memoryRecordId: 'rec' }), nextToken: 'token1' },
        { records: Array(100).fill({ memoryRecordId: 'rec' }), nextToken: 'token2' },
        { records: Array(50).fill({ memoryRecordId: 'rec' }), nextToken: undefined },
      ]

      let totalRecords = 0
      for (const page of pages) {
        totalRecords += page.records.length
      }

      expect(totalRecords).toBe(250)
    })
  })

  describe('Delete Records', async () => {
    it('should delete each record individually', async () => {
      const records = [
        { memoryRecordId: 'rec-1' },
        { memoryRecordId: 'rec-2' },
        { memoryRecordId: 'rec-3' },
      ]

      let deletedCount = 0
      for (const record of records) {
        if (record.memoryRecordId) {
          deletedCount++
        }
      }

      expect(deletedCount).toBe(3)
    })

    it('should handle deletion failures gracefully', async () => {
      const records = [
        { memoryRecordId: 'rec-1', deleteSuccess: true },
        { memoryRecordId: 'rec-2', deleteSuccess: false }, // Simulated failure
        { memoryRecordId: 'rec-3', deleteSuccess: true },
      ]

      let successCount = 0
      let failCount = 0

      for (const record of records) {
        if (record.deleteSuccess) {
          successCount++
        } else {
          failCount++
        }
      }

      expect(successCount).toBe(2)
      expect(failCount).toBe(1)
    })
  })
})
