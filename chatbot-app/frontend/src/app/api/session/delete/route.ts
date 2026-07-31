/**
 * Session Delete API - Delete a specific chat session
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'

// Check if running in local development mode
const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

export const runtime = 'nodejs'

export async function DELETE(request: NextRequest) {
  try {
    // Extract user from Cognito JWT token
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    // Get session ID from query parameter
    const searchParams = request.nextUrl.searchParams
    const sessionId = searchParams.get('session_id')

    if (!sessionId) {
      return NextResponse.json(
        {
          success: false,
          error: 'Missing session_id parameter',
        },
        { status: 400 }
      )
    }

    console.log(`[API] Deleting session ${sessionId} for user ${userId}`)

    if (userId === 'anonymous') {
      // Anonymous user - delete from local file storage
      if (IS_LOCAL) {
        const { deleteSession } = await import('@/lib/local-session-store')
        deleteSession(userId, sessionId)
        console.log(`[API] Deleted session ${sessionId} from local file for anonymous user`)
      } else {
        // AWS: Anonymous users don't persist sessions
        console.log(`[API] Anonymous user in AWS mode - no session to delete`)
      }
    } else {
      // Authenticated user - delete from DynamoDB (AWS) or local file (local)
      if (IS_LOCAL) {
        const { deleteSession } = await import('@/lib/local-session-store')
        deleteSession(userId, sessionId)
        console.log(`[API] Deleted session ${sessionId} from local file for user ${userId}`)
      } else {
        // AWS: Delete from DynamoDB
        const { deleteSession: deleteDynamoSession } = await import('@/lib/dynamodb-client')
        await deleteDynamoSession(userId, sessionId)
        console.log(`[API] Deleted session ${sessionId} from DynamoDB for user ${userId}`)
      }
    }

    return NextResponse.json({
      success: true,
      message: 'Session deleted successfully',
    })
  } catch (error) {
    console.error('[API] Error deleting session:', error)

    return NextResponse.json(
      {
        success: false,
        error: 'Failed to delete session',
        message: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    )
  }
}
