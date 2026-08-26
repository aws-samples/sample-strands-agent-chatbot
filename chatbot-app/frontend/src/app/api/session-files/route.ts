import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { DynamoSessionFileRepository } from '@/lib/session-files/repository'

export async function GET(request: NextRequest) {
  try {
    const sessionId = request.headers.get('X-Session-ID')
      || request.nextUrl.searchParams.get('sessionId')
    if (!sessionId) {
      return NextResponse.json({ error: 'Session ID required' }, { status: 400 })
    }
    const user = await extractUserFromRequest(request)
    const repository = new DynamoSessionFileRepository()
    const page = await repository.list(
      user.userId,
      sessionId,
      request.nextUrl.searchParams.get('cursor') || undefined,
    )
    return NextResponse.json(page)
  } catch (error) {
    console.error('[SessionFiles] Error:', error)
    return NextResponse.json(
      { error: 'Failed to list session files' },
      { status: 500 },
    )
  }
}
