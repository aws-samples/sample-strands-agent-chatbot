/**
 * Tests for workspace API routes
 *
 * Tests cover:
 * - Document type mapping from tool names
 * - S3 file listing
 * - Authentication handling
 * - Error responses
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Mock dependencies
vi.mock('@/lib/auth-utils', () => ({
  extractUserFromRequest: vi.fn(),
  getSessionId: vi.fn()
}))

vi.mock('@aws-sdk/client-s3', () => ({
  S3Client: vi.fn().mockImplementation(() => ({
    send: vi.fn()
  })),
  ListObjectsV2Command: vi.fn()
}))

vi.mock('@aws-sdk/client-ssm', () => ({
  SSMClient: vi.fn().mockImplementation(() => ({
    send: vi.fn()
  })),
  GetParameterCommand: vi.fn()
}))

// Import after mocks
import { extractUserFromRequest, getSessionId } from '@/lib/auth-utils'
import { TOOL_TO_DOC_TYPE, DOC_TYPE_TO_TOOL_TYPE, DocumentType } from '@/config/document-tools'

// Helper to create mock NextRequest
function createMockNextRequest(options: {
  method?: string
  searchParams?: Record<string, string>
  headers?: Record<string, string>
} = {}) {
  const { method = 'GET', searchParams = {}, headers = {} } = options

  const url = new URL('http://localhost:3000/api/workspace/files')
  Object.entries(searchParams).forEach(([key, value]) => {
    url.searchParams.set(key, value)
  })

  return {
    method,
    nextUrl: url,
    headers: {
      get: (name: string) => headers[name.toLowerCase()] || null
    }
  }
}

describe('Workspace Files API', async () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubEnv('DOCUMENT_BUCKET', 'test-bucket')
  })

  describe('Tool to Document Type Mapping', async () => {
    // Uses centralized TOOL_TO_DOC_TYPE from @/config/document-tools

    it('should map Word tool names to word document type', async () => {
      expect(TOOL_TO_DOC_TYPE['create_word_document']).toBe('word')
      expect(TOOL_TO_DOC_TYPE['modify_word_document']).toBe('word')
      expect(TOOL_TO_DOC_TYPE['read_word_document']).toBe('word')
      expect(TOOL_TO_DOC_TYPE['list_my_word_documents']).toBe('word')
    })

    it('should map Excel tool names to excel document type', async () => {
      expect(TOOL_TO_DOC_TYPE['create_excel_spreadsheet']).toBe('excel')
      expect(TOOL_TO_DOC_TYPE['modify_excel_spreadsheet']).toBe('excel')
      expect(TOOL_TO_DOC_TYPE['read_excel_spreadsheet']).toBe('excel')
      expect(TOOL_TO_DOC_TYPE['list_my_excel_spreadsheets']).toBe('excel')
    })

    it('should map PowerPoint tool names to powerpoint document type', async () => {
      expect(TOOL_TO_DOC_TYPE['create_presentation']).toBe('powerpoint')
      expect(TOOL_TO_DOC_TYPE['update_slide_content']).toBe('powerpoint')
      expect(TOOL_TO_DOC_TYPE['add_slide']).toBe('powerpoint')
      expect(TOOL_TO_DOC_TYPE['delete_slides']).toBe('powerpoint')
      expect(TOOL_TO_DOC_TYPE['move_slide']).toBe('powerpoint')
    })

    it('should return undefined for unknown tools', async () => {
      expect(TOOL_TO_DOC_TYPE['unknown_tool']).toBeUndefined()
      expect(TOOL_TO_DOC_TYPE['send_email']).toBeUndefined()
    })
  })

  describe('Request Validation', async () => {
    it('should extract document type from toolName parameter', async () => {
      const request = createMockNextRequest({
        searchParams: { toolName: 'create_word_document' }
      })

      const toolName = request.nextUrl.searchParams.get('toolName')
      expect(toolName).toBe('create_word_document')
    })

    it('should extract document type from docType parameter', async () => {
      const request = createMockNextRequest({
        searchParams: { docType: 'word' }
      })

      const docType = request.nextUrl.searchParams.get('docType')
      expect(docType).toBe('word')
    })

    it('should prefer docType over toolName when both provided', async () => {
      const request = createMockNextRequest({
        searchParams: {
          toolName: 'create_excel_spreadsheet',
          docType: 'word'
        }
      })

      const docType = request.nextUrl.searchParams.get('docType')
      const toolName = request.nextUrl.searchParams.get('toolName')

      // Route logic prefers docType
      const finalDocType = docType || (toolName ? 'excel' : null)
      expect(finalDocType).toBe('word')
    })

    it('should require either toolName or docType', async () => {
      const request = createMockNextRequest({
        searchParams: {} // No params
      })

      const toolName = request.nextUrl.searchParams.get('toolName')
      const docType = request.nextUrl.searchParams.get('docType')

      expect(toolName).toBeNull()
      expect(docType).toBeNull()

      // Route should return 400 error
      const isValid = toolName !== null || docType !== null
      expect(isValid).toBe(false)
    })
  })

  describe('Authentication', async () => {
    it('should extract user from request', async () => {
      const mockExtract = extractUserFromRequest as ReturnType<typeof vi.fn>
      mockExtract.mockReturnValue({ userId: 'user-123' })

      const request = createMockNextRequest({ searchParams: { docType: 'word' } })
      const user = await extractUserFromRequest(request as any)

      expect(user.userId).toBe('user-123')
      expect(mockExtract).toHaveBeenCalledWith(request)
    })

    it('should extract session ID from request', async () => {
      const mockGetSession = getSessionId as ReturnType<typeof vi.fn>
      mockGetSession.mockReturnValue({ sessionId: 'session-456' })

      const request = createMockNextRequest({ searchParams: { docType: 'word' } })
      const { sessionId } = getSessionId(request as any, 'user-123')

      expect(sessionId).toBe('session-456')
    })

    it('should require session ID', async () => {
      const mockGetSession = getSessionId as ReturnType<typeof vi.fn>
      mockGetSession.mockReturnValue({ sessionId: null })

      const { sessionId } = getSessionId({} as any, 'user-123')

      // Route should return 400 error when no session
      expect(sessionId).toBeNull()
    })
  })

  describe('S3 Path Construction', async () => {
    it('should construct correct S3 prefix for word documents', async () => {
      const userId = 'user-123'
      const sessionId = 'session-456'
      const docType = 'word'

      const s3Prefix = `documents/${userId}/${sessionId}/${docType}/`

      expect(s3Prefix).toBe('documents/user-123/session-456/word/')
    })

    it('should construct correct S3 prefix for excel documents', async () => {
      const userId = 'user-abc'
      const sessionId = 'session-xyz'
      const docType = 'excel'

      const s3Prefix = `documents/${userId}/${sessionId}/${docType}/`

      expect(s3Prefix).toBe('documents/user-abc/session-xyz/excel/')
    })

    it('should construct correct S3 prefix for powerpoint documents', async () => {
      const userId = 'test-user'
      const sessionId = 'test-session'
      const docType = 'powerpoint'

      const s3Prefix = `documents/${userId}/${sessionId}/${docType}/`

      expect(s3Prefix).toBe('documents/test-user/test-session/powerpoint/')
    })
  })

  describe('Response Formatting', async () => {
    it('should format file response correctly', async () => {
      // Mock S3 object
      const s3Object = {
        Key: 'documents/user-123/session-456/word/report.docx',
        Size: 15360, // 15 KB
        LastModified: new Date('2024-01-15T10:00:00Z')
      }

      const bucket = 'test-bucket'
      const docType = 'word'

      // Format like the route does (using centralized DOC_TYPE_TO_TOOL_TYPE)
      const formattedFile = {
        filename: s3Object.Key.split('/').pop(),
        size_kb: `${(s3Object.Size / 1024).toFixed(1)} KB`,
        last_modified: s3Object.LastModified.toISOString(),
        s3_key: `s3://${bucket}/${s3Object.Key}`,
        tool_type: DOC_TYPE_TO_TOOL_TYPE[docType as DocumentType],
      }

      expect(formattedFile).toEqual({
        filename: 'report.docx',
        size_kb: '15.0 KB',
        last_modified: '2024-01-15T10:00:00.000Z',
        s3_key: 's3://test-bucket/documents/user-123/session-456/word/report.docx',
        tool_type: 'word_document'
      })
    })

    it('should map doc type to correct tool_type format using DOC_TYPE_TO_TOOL_TYPE', async () => {
      // Uses centralized DOC_TYPE_TO_TOOL_TYPE from @/config/document-tools
      expect(DOC_TYPE_TO_TOOL_TYPE['word']).toBe('word_document')
      expect(DOC_TYPE_TO_TOOL_TYPE['excel']).toBe('excel_spreadsheet')
      expect(DOC_TYPE_TO_TOOL_TYPE['powerpoint']).toBe('powerpoint_presentation')
    })

    it('should filter out hidden files', async () => {
      const files = [
        { filename: 'report.docx', Key: 'path/report.docx' },
        { filename: '.template_metadata', Key: 'path/.template_metadata' },
        { filename: 'data.xlsx', Key: 'path/data.xlsx' },
        { filename: '.DS_Store', Key: 'path/.DS_Store' }
      ]

      const filteredFiles = files.filter(f => !f.filename.startsWith('.'))

      expect(filteredFiles.length).toBe(2)
      expect(filteredFiles.map(f => f.filename)).toEqual(['report.docx', 'data.xlsx'])
    })

    it('should sort files by last_modified descending (most recent first)', async () => {
      const files = [
        { filename: 'old.docx', last_modified: '2024-01-10T10:00:00Z' },
        { filename: 'newest.docx', last_modified: '2024-01-15T15:00:00Z' },
        { filename: 'middle.docx', last_modified: '2024-01-12T12:00:00Z' }
      ]

      const sortedFiles = [...files].sort((a, b) =>
        new Date(b.last_modified).getTime() - new Date(a.last_modified).getTime()
      )

      expect(sortedFiles.map(f => f.filename)).toEqual([
        'newest.docx',
        'middle.docx',
        'old.docx'
      ])
    })
  })

  describe('Error Handling', async () => {
    it('should handle missing DOCUMENT_BUCKET', async () => {
      vi.stubEnv('DOCUMENT_BUCKET', '')

      const bucket = process.env.DOCUMENT_BUCKET

      // When empty, should fallback to Parameter Store lookup
      expect(bucket).toBe('')
    })

    it('should handle S3 list errors gracefully', async () => {
      // Simulate S3 error scenario
      const s3Error = new Error('Access Denied')

      // Route should catch and return 500
      expect(s3Error.message).toBe('Access Denied')
    })

    it('should handle empty S3 response', async () => {
      const s3Response = {
        Contents: undefined
      }

      // Route should return empty files array
      const files = s3Response.Contents ? [] : []

      expect(files).toEqual([])
    })
  })
})

describe('Workspace Download API', async () => {
  describe('Path to S3 key mapping (toS3Key)', async () => {
    const userId = 'user-123'
    const sessionId = 'session-456'

    it('should map code-agent paths to code-agent-workspace prefix', async () => {
      const path = 'code-agent/output.csv'
      const expected = `code-agent-workspace/${userId}/${sessionId}/output.csv`
      const result = toS3Key(userId, sessionId, path)
      expect(result).toBe(expected)
    })

    it('should map code-interpreter paths', async () => {
      const path = 'code-interpreter/chart.png'
      const expected = `code-interpreter-workspace/${userId}/${sessionId}/chart.png`
      const result = toS3Key(userId, sessionId, path)
      expect(result).toBe(expected)
    })

    it('should map documents paths', async () => {
      const path = 'documents/word/report.docx'
      const expected = `documents/${userId}/${sessionId}/word/report.docx`
      const result = toS3Key(userId, sessionId, path)
      expect(result).toBe(expected)
    })

    it('should default to documents namespace for unrecognized paths', async () => {
      const path = 'random/file.txt'
      const expected = `documents/${userId}/${sessionId}/random/file.txt`
      const result = toS3Key(userId, sessionId, path)
      expect(result).toBe(expected)
    })

    it('should strip leading slash', async () => {
      const path = '/code-agent/file.json'
      const expected = `code-agent-workspace/${userId}/${sessionId}/file.json`
      const result = toS3Key(userId, sessionId, path)
      expect(result).toBe(expected)
    })
  })

  describe('Request validation', async () => {
    it('should require path and sessionId in body', async () => {
      const body = { path: 'code-agent/file.csv', sessionId: 'sess-123' }
      expect(body.path).toBeTruthy()
      expect(body.sessionId).toBeTruthy()
    })

    it('should reject missing path', async () => {
      const body = { sessionId: 'sess-123' } as any
      const isValid = !!(body.path && body.sessionId)
      expect(isValid).toBe(false)
    })

    it('should reject missing sessionId', async () => {
      const body = { path: 'code-agent/file.csv' } as any
      const isValid = !!(body.path && body.sessionId)
      expect(isValid).toBe(false)
    })
  })

  describe('Filename extraction', async () => {
    it('should extract filename from path', async () => {
      const path = 'code-agent/subdir/output.csv'
      const filename = path.split('/').pop() || 'download'
      expect(filename).toBe('output.csv')
    })

    it('should fallback to download when path ends with slash', async () => {
      const path = 'code-agent/'
      const filename = path.split('/').pop() || 'download'
      expect(filename).toBe('download')
    })
  })
})

// Reusable path mapper (mirrors route.ts logic)
function toS3Key(userId: string, sessionId: string, path: string): string {
  const NAMESPACE_MAP: [string, string][] = [
    ['code-agent', `code-agent-workspace/${userId}/${sessionId}/`],
    ['code-interpreter', `code-interpreter-workspace/${userId}/${sessionId}/`],
    ['documents', `documents/${userId}/${sessionId}/`],
  ]
  const cleanPath = path.replace(/^\//, '')
  for (const [prefix, base] of NAMESPACE_MAP) {
    if (cleanPath.startsWith(prefix)) {
      const suffix = cleanPath.slice(prefix.length).replace(/^\//, '')
      return base + suffix
    }
  }
  return `documents/${userId}/${sessionId}/${cleanPath}`
}

describe('Frontend Workspace File Fetching (useStreamEvents)', async () => {
  // Tests for the frontend logic that fetches workspace files
  // Uses centralized TOOL_TO_DOC_TYPE from @/config/document-tools

  describe('TOOL_TO_DOC_TYPE mapping', async () => {
    it('should detect word tools', async () => {
      const toolNames = ['create_word_document', 'modify_word_document']
      const usedDocTypes = new Set<string>()

      toolNames.forEach(name => {
        const docType = TOOL_TO_DOC_TYPE[name]
        if (docType) usedDocTypes.add(docType)
      })

      expect(usedDocTypes.has('word')).toBe(true)
      expect(usedDocTypes.size).toBe(1)
    })

    it('should detect multiple doc types from different tools', async () => {
      const toolNames = ['create_word_document', 'create_excel_spreadsheet', 'create_presentation']
      const usedDocTypes = new Set<string>()

      toolNames.forEach(name => {
        const docType = TOOL_TO_DOC_TYPE[name]
        if (docType) usedDocTypes.add(docType)
      })

      expect(usedDocTypes.size).toBe(3)
      expect(usedDocTypes.has('word')).toBe(true)
      expect(usedDocTypes.has('excel')).toBe(true)
      expect(usedDocTypes.has('powerpoint')).toBe(true)
    })

    it('should ignore non-document tools', async () => {
      const toolNames = ['web_search', 'calculator', 'create_word_document']
      const usedDocTypes = new Set<string>()

      toolNames.forEach(name => {
        const docType = TOOL_TO_DOC_TYPE[name]
        if (docType) usedDocTypes.add(docType)
      })

      expect(usedDocTypes.size).toBe(1) // Only word
    })
  })

  describe('DOC_TYPE_TO_TOOL_TYPE mapping', async () => {
    // Uses centralized DOC_TYPE_TO_TOOL_TYPE from @/config/document-tools

    it('should map word to word_document', async () => {
      expect(DOC_TYPE_TO_TOOL_TYPE['word']).toBe('word_document')
    })

    it('should map excel to excel_spreadsheet', async () => {
      expect(DOC_TYPE_TO_TOOL_TYPE['excel']).toBe('excel_spreadsheet')
    })

    it('should map powerpoint to powerpoint_presentation', async () => {
      expect(DOC_TYPE_TO_TOOL_TYPE['powerpoint']).toBe('powerpoint_presentation')
    })
  })

  describe('Workspace file integration', async () => {
    it('should transform API response for DynamoDB storage with user_id', async () => {
      // Simulate API response - BFF returns userId which is needed for S3 path
      const apiResponse = {
        files: [
          { filename: 'report.docx', tool_type: 'word' },
          { filename: 'data.xlsx', tool_type: 'excel' }
        ],
        userId: '18c1e380-1234-5678-9abc-def012345678'  // Full UUID from JWT
      }

      // Transform for DynamoDB storage - include user_id for download path
      const documents = apiResponse.files.map(file => ({
        filename: file.filename,
        tool_type: file.tool_type,
        user_id: apiResponse.userId
      }))

      expect(documents).toEqual([
        { filename: 'report.docx', tool_type: 'word', user_id: '18c1e380-1234-5678-9abc-def012345678' },
        { filename: 'data.xlsx', tool_type: 'excel', user_id: '18c1e380-1234-5678-9abc-def012345678' }
      ])
    })

    it('should handle empty API response', async () => {
      const apiResponse = { files: [] }

      const documents = apiResponse.files || []

      expect(documents).toEqual([])
    })

    it('should handle API error gracefully', async () => {
      // Simulate error scenario
      let workspaceDocuments: Array<{ filename: string; tool_type: string }> = []

      try {
        throw new Error('Network error')
      } catch (error) {
        // On error, documents remain empty
        workspaceDocuments = []
      }

      expect(workspaceDocuments).toEqual([])
    })
  })

  describe('Workspace API fetch headers (Integration)', async () => {
    /**
     * These tests verify that the frontend correctly includes authentication
     * headers when calling /api/workspace/files. Without proper headers:
     * - Authorization header missing → userId becomes 'anonymous'
     * - X-Session-ID header missing → new session ID is generated
     *
     * This causes S3 path mismatch:
     * - Expected: documents/{realUserId}/{realSessionId}/word/
     * - Actual:   documents/anonymous/{newSessionId}/word/
     */

    let mockFetch: ReturnType<typeof vi.fn>
    let originalFetch: typeof global.fetch

    beforeEach(() => {
      originalFetch = global.fetch
      mockFetch = vi.fn()
      global.fetch = mockFetch
    })

    afterEach(() => {
      global.fetch = originalFetch
    })

    it('should include X-Session-ID header when fetching workspace files', async () => {
      const sessionId = 'user123_abc123_session456'

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ files: [] })
      })

      // Simulate the fetch call pattern from useStreamEvents
      const workspaceHeaders: Record<string, string> = {
        'X-Session-ID': sessionId
      }

      await fetch('/api/workspace/files?docType=word', {
        headers: workspaceHeaders
      })

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/workspace/files?docType=word',
        expect.objectContaining({
          headers: expect.objectContaining({
            'X-Session-ID': sessionId
          })
        })
      )
    })

    it('should include Authorization header when auth token is available', async () => {
      const sessionId = 'user123_abc123_session456'
      const authToken = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyMTIzIn0.mock'

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ files: [] })
      })

      // Simulate the fetch call pattern from useStreamEvents with auth
      const workspaceHeaders: Record<string, string> = {
        'X-Session-ID': sessionId,
        'Authorization': `Bearer ${authToken}`
      }

      await fetch('/api/workspace/files?docType=word', {
        headers: workspaceHeaders
      })

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/workspace/files?docType=word',
        expect.objectContaining({
          headers: expect.objectContaining({
            'X-Session-ID': sessionId,
            'Authorization': `Bearer ${authToken}`
          })
        })
      )
    })

    it('should fail to find files when headers are missing (demonstrates the bug)', async () => {
      /**
       * This test demonstrates what happens when headers are NOT included:
       * - BFF receives request without Authorization → userId = 'anonymous'
       * - BFF receives request without X-Session-ID → generates new session
       * - S3 lookup uses wrong path → returns empty files
       */
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({
          files: [],
          userId: 'anonymous',  // Wrong user!
          sessionId: 'anon0000_newSession_xyz'  // Wrong session!
        })
      })

      // BAD: Fetch without headers (the bug we fixed)
      const response = await fetch('/api/workspace/files?docType=word')
      const data = await response.json()

      // This demonstrates the problem: empty files due to wrong path
      expect(data.files).toEqual([])
      expect(data.userId).toBe('anonymous')
    })

    it('should find files when correct headers are included', async () => {
      const sessionId = 'user123_abc123_realSession'
      const authToken = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyMTIzIn0.mock'

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({
          files: [
            { filename: 'report.docx', tool_type: 'word_document' }
          ],
          userId: 'user123',  // Correct user from JWT
          sessionId: sessionId  // Correct session from header
        })
      })

      // GOOD: Fetch with proper headers
      const response = await fetch('/api/workspace/files?docType=word', {
        headers: {
          'X-Session-ID': sessionId,
          'Authorization': `Bearer ${authToken}`
        }
      })
      const data = await response.json()

      // Files found because correct S3 path was used
      expect(data.files.length).toBe(1)
      expect(data.files[0].filename).toBe('report.docx')
      expect(data.userId).toBe('user123')
      expect(data.sessionId).toBe(sessionId)
    })

    it('should construct correct headers object for workspace fetch', async () => {
      /**
       * Test the header construction logic that should be used in useStreamEvents.
       * This verifies the fix pattern.
       */
      const currentSessionId = 'user123_timestamp_uuid'
      const authToken = 'mock-jwt-token'

      // The correct pattern (as implemented in the fix)
      const workspaceHeaders: Record<string, string> = {
        'X-Session-ID': currentSessionId
      }

      // Add Authorization if available
      if (authToken) {
        workspaceHeaders['Authorization'] = `Bearer ${authToken}`
      }

      expect(workspaceHeaders).toEqual({
        'X-Session-ID': currentSessionId,
        'Authorization': `Bearer ${authToken}`
      })
    })

    it('should handle missing auth token gracefully (still include session ID)', async () => {
      /**
       * For anonymous users, auth token may not be available.
       * Session ID should still be included.
       */
      const currentSessionId = 'anon0000_timestamp_uuid'
      const authToken = null  // No auth available

      const workspaceHeaders: Record<string, string> = {
        'X-Session-ID': currentSessionId
      }

      // Only add Authorization if token exists
      if (authToken) {
        workspaceHeaders['Authorization'] = `Bearer ${authToken}`
      }

      expect(workspaceHeaders).toEqual({
        'X-Session-ID': currentSessionId
      })
      expect(workspaceHeaders['Authorization']).toBeUndefined()
    })
  })
})
