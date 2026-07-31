/**
 * Voice Session End API
 *
 * Called after WebSocket connection closes.
 * Updates session metadata (lastActivity, title, messageCount).
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

export const runtime = 'nodejs'

export async function POST(request: NextRequest) {
  try {
    // 1. Authentication
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    // 2. Get session info from request body
    const body = await request.json()
    const { sessionId, messageCount, title } = body

    if (!sessionId) {
      return NextResponse.json(
        { success: false, error: 'sessionId is required' },
        { status: 400 }
      )
    }

    console.log(`[Voice End] User: ${userId}, Session: ${sessionId}, Messages: ${messageCount}`)

    // 3. Update session metadata
    const updateData: Record<string, any> = {
      lastMessageAt: new Date().toISOString(),
    }

    if (typeof messageCount === 'number') {
      updateData.messageCount = messageCount
    }

    if (title) {
      updateData.title = title
    }

    if (IS_LOCAL) {
      const { updateSession, getSession } = await import('@/lib/local-session-store')
      const session = getSession(userId, sessionId)
      if (session) {
        updateSession(userId, sessionId, updateData)
        console.log(`[Voice End] Local session updated: ${sessionId}`)
      }
    } else {
      const { updateSession, getSession } = await import('@/lib/dynamodb-client')
      const session = await getSession(userId, sessionId)
      if (session) {
        await updateSession(userId, sessionId, updateData)
        console.log(`[Voice End] DynamoDB session updated: ${sessionId}`)
      }
    }

    return NextResponse.json({
      success: true,
      message: 'Voice session ended successfully',
    })
  } catch (error) {
    console.error('[Voice End] Error:', error)
    return NextResponse.json(
      {
        success: false,
        error: error instanceof Error ? error.message : 'Failed to end voice session',
      },
      { status: 500 }
    )
  }
}
