import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import {
  S3WorkspaceRepository,
  WorkspacePathError,
} from '@/lib/workspace/s3-repository'

export async function GET(request: NextRequest) {
  try {
    const sessionId = request.headers.get('X-Session-ID')
    if (!sessionId) {
      return NextResponse.json({ error: 'Session ID required' }, { status: 400 })
    }

    const user = await extractUserFromRequest(request)
    const repository = new S3WorkspaceRepository()
    const page = await repository.list(
      user.userId,
      sessionId,
      request.nextUrl.searchParams.get('path') || '',
      request.nextUrl.searchParams.get('cursor') || undefined,
    )
    return NextResponse.json(page)
  } catch (error) {
    if (error instanceof WorkspacePathError) {
      return NextResponse.json({ error: error.message }, { status: 400 })
    }
    console.error('[WorkspaceEntries] Error:', error)
    return NextResponse.json(
      { error: 'Failed to list workspace entries' },
      { status: 500 },
    )
  }
}
