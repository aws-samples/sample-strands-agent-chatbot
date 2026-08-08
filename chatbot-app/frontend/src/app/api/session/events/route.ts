import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { listSessionEvents } from '@/lib/session-events'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function GET(request: NextRequest) {
  try {
    const sessionId = request.nextUrl.searchParams.get('session_id')
    if (!sessionId) {
      return NextResponse.json(
        { error: 'Missing session_id parameter' },
        { status: 400 },
      )
    }

    const user = await extractUserFromRequest(request)
    const events = await listSessionEvents(user.userId, sessionId)
    return NextResponse.json({ events })
  } catch (error) {
    console.error('[SessionEvents] Failed to list events:', error)
    return NextResponse.json(
      { error: 'Failed to list session events' },
      { status: 500 },
    )
  }
}
