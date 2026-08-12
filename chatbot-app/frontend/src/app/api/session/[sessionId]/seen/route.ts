import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { markSessionAttentionSeen } from '@/lib/session-events'

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

export const runtime = 'nodejs'

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
) {
  try {
    const { sessionId } = await params
    const user = await extractUserFromRequest(request)
    const body = await request.json()
    const seenThroughCursor = body?.seenThroughCursor

    if (
      !sessionId ||
      typeof seenThroughCursor !== 'string' ||
      !seenThroughCursor.startsWith('OUTBOX_V2#')
    ) {
      return NextResponse.json(
        { success: false, error: 'A valid seenThroughCursor is required' },
        { status: 400 },
      )
    }

    let session = null
    if (IS_LOCAL) {
      const { getSession } = await import('@/lib/local-session-store')
      session = getSession(user.userId, sessionId)
    } else if (user.userId !== 'anonymous') {
      const { getSession } = await import('@/lib/dynamodb-client')
      session = await getSession(user.userId, sessionId)
    }
    if (!session) {
      return NextResponse.json(
        { success: false, error: 'Session not found' },
        { status: 404 },
      )
    }

    await markSessionAttentionSeen(
      user.userId,
      sessionId,
      seenThroughCursor,
    )
    return NextResponse.json({ success: true })
  } catch (error) {
    console.error('[SessionAttention] Failed to mark session seen:', error)
    return NextResponse.json(
      { success: false, error: 'Failed to update session attention' },
      { status: 500 },
    )
  }
}
