import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import {
  S3WorkspaceRepository,
  WorkspacePathError,
} from '@/lib/workspace/s3-repository'

export async function GET(request: NextRequest) {
  try {
    const sessionId = request.headers.get('X-Session-ID')
    const path = request.nextUrl.searchParams.get('path')
    if (!sessionId) {
      return NextResponse.json({ error: 'Session ID required' }, { status: 400 })
    }
    if (!path) {
      return NextResponse.json({ error: 'File path required' }, { status: 400 })
    }

    const user = await extractUserFromRequest(request)
    const repository = new S3WorkspaceRepository()
    const preview = await repository.preview(user.userId, sessionId, path)
    return NextResponse.json(preview)
  } catch (error) {
    if (error instanceof WorkspacePathError) {
      return NextResponse.json({ error: error.message }, { status: 400 })
    }
    console.error('[WorkspacePreview] Error:', error)
    return NextResponse.json(
      { error: 'Failed to preview workspace file' },
      { status: 500 },
    )
  }
}
