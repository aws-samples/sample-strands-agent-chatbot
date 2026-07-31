/**
 * Update browser session info in session metadata
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

export const runtime = 'nodejs'

export async function POST(request: NextRequest) {
  try {
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    const { sessionId, browserSession } = await request.json()

    if (!sessionId || !browserSession) {
      return NextResponse.json(
        { success: false, error: 'sessionId and browserSession required' },
        { status: 400 }
      )
    }

    console.log(`[API] Updating browser session for chat session ${sessionId}:`, browserSession)

    if (IS_LOCAL) {
      const { updateSession, getSession } = await import('@/lib/local-session-store')
      const session = getSession(userId, sessionId)
      if (session) {
        updateSession(userId, sessionId, {
          metadata: {
            ...session.metadata,
            browserSession
          }
        })
      }
    } else {
      const { updateSession, getSession } = await import('@/lib/dynamodb-client')
      const session = await getSession(userId, sessionId)
      if (session) {
        await updateSession(userId, sessionId, {
          metadata: {
            ...session.metadata,
            browserSession
          }
        })
      }
    }

    return NextResponse.json({ success: true })
  } catch (error) {
    console.error('[API] Error updating browser session:', error)
    return NextResponse.json(
      {
        success: false,
        error: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    )
  }
}
