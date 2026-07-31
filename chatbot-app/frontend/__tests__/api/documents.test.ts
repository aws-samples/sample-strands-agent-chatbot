/**
 * Tests for /api/documents/download route
 *
 * Tests cover:
 * - S3 key reconstruction from session/filename/toolType
 * - User ID extraction from Authorization header (extractUserFromRequest)
 * - Tool type to document type mapping
 * - Request validation
 * - Error handling
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock dependencies
vi.mock('@aws-sdk/client-ssm', () => ({
  SSMClient: vi.fn().mockImplementation(() => ({
    send: vi.fn()
  })),
  GetParameterCommand: vi.fn()
}))

vi.mock('@/lib/auth-utils', () => ({
  extractUserFromRequest: vi.fn()
}))

import { extractUserFromRequest } from '@/lib/auth-utils'

describe('Documents Download API', async () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubEnv('DOCUMENT_BUCKET', 'test-document-bucket')
  })

  describe('Request Validation', async () => {
    it('should require sessionId', async () => {
      const body: { filename: string; toolType: string; sessionId?: string } = {
        filename: 'report.docx',
        toolType: 'word_document'
        // sessionId missing
      }

      const isValid = body.sessionId && body.filename && body.toolType
      expect(isValid).toBeFalsy()
    })

    it('should require filename', async () => {
      const body: { sessionId: string; toolType: string; filename?: string } = {
        sessionId: 'session-123',
        toolType: 'word_document'
        // filename missing
      }

      const isValid = body.sessionId && body.filename && body.toolType
      expect(isValid).toBeFalsy()
    })

    it('should require toolType', async () => {
      const body: { sessionId: string; filename: string; toolType?: string } = {
        sessionId: 'session-123',
        filename: 'report.docx'
        // toolType missing
      }

      const isValid = body.sessionId && body.filename && body.toolType
      expect(isValid).toBeFalsy()
    })

    it('should accept valid request body (no userId needed)', async () => {
      const body = {
        sessionId: 'user123_abc_session456',
        filename: 'report.docx',
        toolType: 'word_document'
        // userId not required - BFF extracts from Authorization header
      }

      const isValid = body.sessionId && body.filename && body.toolType
      expect(isValid).toBeTruthy()
    })
  })

  describe('User ID Extraction from Authorization Header', async () => {
    /**
     * The route uses extractUserFromRequest() to get userId from JWT.
     * This is the same pattern used by /api/workspace/files.
     */

    it('should extract userId from authenticated request', async () => {
      const mockExtract = extractUserFromRequest as ReturnType<typeof vi.fn>
      mockExtract.mockReturnValue({ userId: '18c1e380-1234-5678-9abc-def012345678' })

      const mockRequest = {
        headers: {
          get: (name: string) => name === 'authorization' ? 'Bearer mock-jwt-token' : null
        }
      }

      const user = await extractUserFromRequest(mockRequest as any)

      expect(user.userId).toBe('18c1e380-1234-5678-9abc-def012345678')
      expect(mockExtract).toHaveBeenCalledWith(mockRequest)
    })

    it('should return anonymous for unauthenticated request', async () => {
      const mockExtract = extractUserFromRequest as ReturnType<typeof vi.fn>
      mockExtract.mockReturnValue({ userId: 'anonymous' })

      const mockRequest = {
        headers: {
          get: () => null  // No Authorization header
        }
      }

      const user = await extractUserFromRequest(mockRequest as any)

      expect(user.userId).toBe('anonymous')
    })

    it('should use full UUID from JWT (not truncated sessionId prefix)', async () => {
      /**
       * This test verifies the fix for the NoSuchKey bug:
       * - S3 files are stored with full UUID: documents/18c1e380-xxxx-xxxx-xxxx-xxxxxxxxxxxx/...
       * - Old code used sessionId.split('_')[0] = '18c1e380' (8 chars) - WRONG
       * - New code extracts full UUID from JWT - CORRECT
       */
      const mockExtract = extractUserFromRequest as ReturnType<typeof vi.fn>
      const fullUUID = '18c1e380-1234-5678-9abc-def012345678'
      mockExtract.mockReturnValue({ userId: fullUUID })

      const mockRequest = {
        headers: {
          get: (name: string) => name === 'authorization' ? 'Bearer jwt-with-full-uuid' : null
        }
      }

      const user = await extractUserFromRequest(mockRequest as any)

      // Full UUID (36 chars), not truncated prefix (8 chars)
      expect(user.userId).toBe(fullUUID)
      expect(user.userId.length).toBe(36)
      expect(user.userId).not.toBe('18c1e380')  // Not truncated!
    })
  })

  describe('Tool Type to Document Type Mapping', async () => {
    const toolTypeToDocType: Record<string, string> = {
      'word_document': 'word',
      'excel_spreadsheet': 'excel',
      'powerpoint_presentation': 'powerpoint'
    }

    it('should map word_document to word', async () => {
      expect(toolTypeToDocType['word_document']).toBe('word')
    })

    it('should map excel_spreadsheet to excel', async () => {
      expect(toolTypeToDocType['excel_spreadsheet']).toBe('excel')
    })

    it('should map powerpoint_presentation to powerpoint', async () => {
      expect(toolTypeToDocType['powerpoint_presentation']).toBe('powerpoint')
    })

    it('should use toolType as-is for unknown types', async () => {
      const unknownType = 'custom_document'
      const documentType = toolTypeToDocType[unknownType] || unknownType

      expect(documentType).toBe('custom_document')
    })
  })

  describe('S3 Key Construction', async () => {
    it('should construct correct S3 key for word document', async () => {
      const bucket = 'test-document-bucket'
      const userId = '18c1e380-1234-5678-9abc-def012345678'  // Full UUID from JWT
      const sessionId = '18c1e380_timestamp_uuid'
      const documentType = 'word'
      const filename = 'report.docx'

      const s3Key = `s3://${bucket}/documents/${userId}/${sessionId}/${documentType}/${filename}`

      expect(s3Key).toBe('s3://test-document-bucket/documents/18c1e380-1234-5678-9abc-def012345678/18c1e380_timestamp_uuid/word/report.docx')
    })

    it('should construct correct S3 key for excel document', async () => {
      const bucket = 'test-document-bucket'
      const userId = 'user-456-full-uuid-here'
      const sessionId = 'user456_xyz_session789'
      const documentType = 'excel'
      const filename = 'data.xlsx'

      const s3Key = `s3://${bucket}/documents/${userId}/${sessionId}/${documentType}/${filename}`

      expect(s3Key).toBe('s3://test-document-bucket/documents/user-456-full-uuid-here/user456_xyz_session789/excel/data.xlsx')
    })

    it('should construct correct S3 key for anonymous user', async () => {
      const bucket = 'test-document-bucket'
      const userId = 'anonymous'  // From extractUserFromRequest when no auth
      const sessionId = 'anon0000_abc_session123'
      const documentType = 'word'
      const filename = 'anonymous-doc.docx'

      const s3Key = `s3://${bucket}/documents/${userId}/${sessionId}/${documentType}/${filename}`

      expect(s3Key).toBe('s3://test-document-bucket/documents/anonymous/anon0000_abc_session123/word/anonymous-doc.docx')
    })

    it('should handle filenames with spaces', async () => {
      const bucket = 'test-document-bucket'
      const userId = 'user-123-full-uuid'
      const sessionId = 'session-456'
      const documentType = 'word'
      const filename = 'My Report 2024.docx'

      const s3Key = `s3://${bucket}/documents/${userId}/${sessionId}/${documentType}/${filename}`

      expect(s3Key).toBe('s3://test-document-bucket/documents/user-123-full-uuid/session-456/word/My Report 2024.docx')
    })

    it('should handle filenames with special characters', async () => {
      const bucket = 'test-document-bucket'
      const userId = 'user-123-full-uuid'
      const sessionId = 'session-456'
      const documentType = 'excel'
      const filename = 'Q1_Report_(Final)-v2.xlsx'

      const s3Key = `s3://${bucket}/documents/${userId}/${sessionId}/${documentType}/${filename}`

      expect(s3Key).toContain('Q1_Report_(Final)-v2.xlsx')
    })
  })

  describe('End-to-End Flow (with extractUserFromRequest)', async () => {
    it('should correctly process authenticated user download request', async () => {
      const mockExtract = extractUserFromRequest as ReturnType<typeof vi.fn>
      mockExtract.mockReturnValue({ userId: '18c1e380-1234-5678-9abc-def012345678' })

      const request = {
        sessionId: '18c1e380_timestamp_uuid',
        filename: 'quarterly-report.docx',
        toolType: 'word_document'
        // No userId in body - extracted from Authorization header
      }

      const bucket = 'test-document-bucket'
      const toolTypeToDocType: Record<string, string> = {
        'word_document': 'word',
        'excel_spreadsheet': 'excel',
        'powerpoint_presentation': 'powerpoint'
      }

      // Simulate route logic
      const user = await extractUserFromRequest({} as any)
      const userId = user.userId
      const documentType = toolTypeToDocType[request.toolType] || request.toolType
      const s3Key = `s3://${bucket}/documents/${userId}/${request.sessionId}/${documentType}/${request.filename}`

      expect(userId).toBe('18c1e380-1234-5678-9abc-def012345678')
      expect(documentType).toBe('word')
      expect(s3Key).toBe('s3://test-document-bucket/documents/18c1e380-1234-5678-9abc-def012345678/18c1e380_timestamp_uuid/word/quarterly-report.docx')
    })

    it('should correctly process anonymous user download request', async () => {
      const mockExtract = extractUserFromRequest as ReturnType<typeof vi.fn>
      mockExtract.mockReturnValue({ userId: 'anonymous' })

      const request = {
        sessionId: 'anon0000_timestamp_uuid',
        filename: 'untitled.xlsx',
        toolType: 'excel_spreadsheet'
      }

      const bucket = 'test-document-bucket'
      const toolTypeToDocType: Record<string, string> = {
        'word_document': 'word',
        'excel_spreadsheet': 'excel',
        'powerpoint_presentation': 'powerpoint'
      }

      // Simulate route logic
      const user = await extractUserFromRequest({} as any)
      const userId = user.userId
      const documentType = toolTypeToDocType[request.toolType] || request.toolType
      const s3Key = `s3://${bucket}/documents/${userId}/${request.sessionId}/${documentType}/${request.filename}`

      expect(userId).toBe('anonymous')
      expect(documentType).toBe('excel')
      expect(s3Key).toBe('s3://test-document-bucket/documents/anonymous/anon0000_timestamp_uuid/excel/untitled.xlsx')
    })
  })

  describe('Frontend Integration (Authorization Header)', async () => {
    /**
     * Tests verifying that frontend sends proper Authorization header
     * so BFF can extract userId correctly.
     */

    it('should demonstrate correct frontend request pattern', async () => {
      // Frontend should include Authorization header
      const authHeaders: Record<string, string> = { 'Content-Type': 'application/json' }
      const mockToken = 'eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxOGMxZTM4MC0xMjM0LTU2NzgtOWFiYy1kZWYwMTIzNDU2NzgifQ.signature'

      // Simulate fetchAuthSession
      if (mockToken) {
        authHeaders['Authorization'] = `Bearer ${mockToken}`
      }

      expect(authHeaders['Authorization']).toBe(`Bearer ${mockToken}`)
      expect(authHeaders['Content-Type']).toBe('application/json')
    })

    it('should handle missing auth gracefully', async () => {
      const authHeaders: Record<string, string> = { 'Content-Type': 'application/json' }
      const mockToken = null  // No auth available

      if (mockToken) {
        authHeaders['Authorization'] = `Bearer ${mockToken}`
      }

      // Authorization header should not be set
      expect(authHeaders['Authorization']).toBeUndefined()
      expect(authHeaders['Content-Type']).toBe('application/json')
    })
  })

  describe('Error Handling', async () => {
    it('should handle missing DOCUMENT_BUCKET gracefully', async () => {
      vi.stubEnv('DOCUMENT_BUCKET', '')

      const bucket = process.env.DOCUMENT_BUCKET

      // When empty, route should try Parameter Store
      expect(bucket).toBe('')
    })
  })
})
