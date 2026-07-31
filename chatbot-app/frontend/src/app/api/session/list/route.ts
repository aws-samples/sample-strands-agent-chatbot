/**
 * Session List API - Get user's chat sessions
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { getUserSessions } from '@/lib/dynamodb-client'

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
    const limit = parseInt(searchParams.get('limit') || '20')
    const status = searchParams.get('status') as 'active' | 'archived' | 'deleted' | undefined

    console.log(`[API] Loading sessions for user ${userId}, limit: ${limit}, status: ${status || 'all'}`)

    let sessions: any[] = []

    if (userId === 'anonymous') {
      // Anonymous user - load from local file storage
      if (IS_LOCAL) {
        const { getUserSessions: getLocalSessions } = await import('@/lib/local-session-store')
        sessions = getLocalSessions(userId, limit, status)
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
        sessions = getLocalSessions(userId, limit, status)
        console.log(`[API] Loaded ${sessions.length} sessions from local file for user ${userId}`)
      } else {
        // AWS: Load from DynamoDB
        sessions = await getUserSessions(userId, limit, status)
        console.log(`[API] Loaded ${sessions.length} sessions from DynamoDB for user ${userId}`)
      }
    }

    return NextResponse.json({
      success: true,
      sessions: sessions.map((s) => ({
        sessionId: s.sessionId,
        title: s.title,
        lastMessageAt: s.lastMessageAt,
        messageCount: s.messageCount,
        starred: s.starred || false,
        status: s.status,
        createdAt: s.createdAt,
        tags: s.tags || [],
      })),
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
