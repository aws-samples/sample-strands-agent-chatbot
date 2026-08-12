/**
 * Session List API - Get user's chat sessions
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { getUserSessions } from '@/lib/dynamodb-client'
import {
  getSessionAttentionStates,
  hasUnseenSessionAttention,
} from '@/lib/session-events'

// Check if running in local development mode
const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

export const runtime = 'nodejs'

export async function GET(request: NextRequest) {
  try {
    // Extract user from Cognito JWT token
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    // Get query parameters
    const searchParams = request.nextUrl.searchParams
    const requestedLimit = parseInt(searchParams.get('limit') || '20')
    const limit = Number.isFinite(requestedLimit)
      ? Math.min(Math.max(requestedLimit, 1), 100)
      : 20
    const status = searchParams.get('status') as 'active' | 'archived' | 'deleted' | undefined

    console.log(`[API] Loading sessions for user ${userId}, limit: ${limit}, status: ${status || 'all'}`)

    let sessions: any[] = []

    if (userId === 'anonymous') {
      // Anonymous user - load from local file storage
      if (IS_LOCAL) {
        const { getUserSessions: getLocalSessions } = await import('@/lib/local-session-store')
        sessions = getLocalSessions(userId, 100, status)
        console.log(`[API] Loaded ${sessions.length} sessions from local file for anonymous user`)
      } else {
        // AWS: Anonymous users don't persist sessions
        sessions = []
        console.log(`[API] Anonymous user in AWS mode - no sessions`)
      }
    } else {
      // Authenticated user - load from DynamoDB (AWS) or local file (local)
      if (IS_LOCAL) {
        const { getUserSessions: getLocalSessions } = await import('@/lib/local-session-store')
        sessions = getLocalSessions(userId, 100, status)
        console.log(`[API] Loaded ${sessions.length} sessions from local file for user ${userId}`)
      } else {
        // AWS: Load from DynamoDB
        sessions = await getUserSessions(userId, 100, status)
        console.log(`[API] Loaded ${sessions.length} sessions from DynamoDB for user ${userId}`)
      }
    }

    let attentionStates = new Map()
    try {
      attentionStates = await getSessionAttentionStates(
        userId,
        sessions.map(session => session.sessionId),
      )
    } catch (error) {
      console.warn('[API] Failed to load session attention state:', error)
    }
    const projectedSessions = sessions.map((session) => {
      const attention = attentionStates.get(session.sessionId) || {}
      const lastActivityAt =
        attention.latestAttentionAt &&
        Date.parse(attention.latestAttentionAt) > Date.parse(session.lastMessageAt)
          ? attention.latestAttentionAt
          : session.lastMessageAt
      return {
        sessionId: session.sessionId,
        title: session.title,
        lastMessageAt: session.lastMessageAt,
        lastActivityAt,
        latestAttentionCursor: attention.latestAttentionCursor,
        lastSeenAttentionCursor: attention.lastSeenAttentionCursor,
        hasUnseenUpdate: hasUnseenSessionAttention(attention),
        messageCount: session.messageCount,
        starred: session.starred || false,
        status: session.status,
        createdAt: session.createdAt,
        tags: session.tags || [],
      }
    })
    projectedSessions.sort((left, right) =>
      Date.parse(right.lastActivityAt) - Date.parse(left.lastActivityAt),
    )

    return NextResponse.json({
      success: true,
      sessions: projectedSessions.slice(0, limit),
    })
  } catch (error) {
    console.error('[API] Error loading sessions:', error)

    return NextResponse.json(
      {
        success: false,
        error: 'Failed to load sessions',
        message: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    )
  }
}
