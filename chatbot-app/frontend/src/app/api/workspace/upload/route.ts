import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import {
  S3WorkspaceRepository,
  WorkspacePathError,
} from '@/lib/workspace/s3-repository'

const MAX_WORKSPACE_UPLOAD_BYTES = 100 * 1024 * 1024

export async function POST(request: NextRequest) {
  try {
    const sessionId = request.headers.get('X-Session-ID')
    if (!sessionId) {
      return NextResponse.json({ error: 'Session ID required' }, { status: 400 })
    }

    const body = await request.json()
    const name = typeof body.name === 'string' ? body.name : ''
    const mimeType = typeof body.mimeType === 'string'
      ? body.mimeType
      : 'application/octet-stream'
    const size = Number(body.size)
    if (!name || !Number.isSafeInteger(size)) {
      return NextResponse.json({ error: 'File required' }, { status: 400 })
    }
    if (size <= 0) {
      return NextResponse.json({ error: 'File is empty' }, { status: 400 })
    }
    if (size > MAX_WORKSPACE_UPLOAD_BYTES) {
      return NextResponse.json(
        { error: 'File exceeds the 100 MB workspace upload limit' },
        { status: 413 },
      )
    }

    const user = await extractUserFromRequest(request)
    const repository = new S3WorkspaceRepository()
    const upload = await repository.createUpload(user.userId, sessionId, {
      name,
      mimeType,
      size,
    })
    return NextResponse.json(upload, { status: 201 })
  } catch (error) {
    if (error instanceof WorkspacePathError) {
      return NextResponse.json({ error: error.message }, { status: 400 })
    }
    console.error('[WorkspaceUpload] Error:', error)
    return NextResponse.json(
      { error: 'Failed to upload workspace file' },
      { status: 500 },
    )
  }
}
