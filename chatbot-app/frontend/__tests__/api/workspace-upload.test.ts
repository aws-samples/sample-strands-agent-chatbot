import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { NextRequest } from 'next/server'

const mocks = vi.hoisted(() => ({
  createUpload: vi.fn(),
}))

vi.mock('@/lib/auth-utils', () => ({
  extractUserFromRequest: vi.fn().mockResolvedValue({ userId: 'user-1' }),
}))

vi.mock('@/lib/workspace/s3-repository', () => ({
  S3WorkspaceRepository: class S3WorkspaceRepository {
    createUpload = mocks.createUpload
  },
  WorkspacePathError: class WorkspacePathError extends Error {},
}))

import { POST } from '@/app/api/workspace/upload/route'

describe('POST /api/workspace/upload', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.createUpload.mockResolvedValue({
      uploadUrl: 'https://uploads.example.test/presigned',
      entry: {
        id: 'uploaded',
        path: 'uploads/data.json',
        parentPath: 'uploads',
        name: 'data.json',
        kind: 'file',
      },
    })
  })

  it('creates a scoped direct-upload ticket', async () => {
    const request = {
      headers: { get: (name: string) => name === 'X-Session-ID' ? 'session-1' : null },
      json: vi.fn().mockResolvedValue({
        name: 'data.json',
        mimeType: 'application/json',
        size: 8,
      }),
    } as unknown as NextRequest

    const response = await POST(request)

    expect(response.status).toBe(201)
    expect(mocks.createUpload).toHaveBeenCalledWith(
      'user-1',
      'session-1',
      {
        name: 'data.json',
        mimeType: 'application/json',
        size: 8,
      },
    )
    expect(await response.json()).toMatchObject({
      uploadUrl: 'https://uploads.example.test/presigned',
    })
  })

  it('requires a session ID', async () => {
    const request = {
      headers: { get: vi.fn().mockReturnValue(null) },
      json: vi.fn(),
    } as unknown as NextRequest

    const response = await POST(request)

    expect(response.status).toBe(400)
    expect(mocks.createUpload).not.toHaveBeenCalled()
  })

  it('rejects upload tickets above the workspace size limit', async () => {
    const request = {
      headers: { get: (name: string) => name === 'X-Session-ID' ? 'session-1' : null },
      json: vi.fn().mockResolvedValue({
        name: 'oversized.jsonl',
        mimeType: 'application/x-ndjson',
        size: 100 * 1024 * 1024 + 1,
      }),
    } as unknown as NextRequest

    const response = await POST(request)

    expect(response.status).toBe(413)
    expect(await response.json()).toEqual({
      error: 'File exceeds the 100 MB workspace upload limit',
    })
    expect(mocks.createUpload).not.toHaveBeenCalled()
  })
})
