/**
 * Session Create API - Create a new chat session immediately
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { v4 as uuidv4 } from 'uuid'

// Check if running in local development mode
const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

export const runtime = 'nodejs'

export async function POST(request: NextRequest) {
  try {
    // Extract user from Cognito JWT token
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    // Generate new session ID
    const newSessionId = uuidv4()
    const now = new Date().toISOString()

    console.log(`[API] Creating new session ${newSessionId} for user ${userId}`)

    // Create session metadata with default title
    const sessionData = {
      title: 'New Chat',
      messageCount: 0,
      lastMessageAt: now,
      status: 'active' as const,
      starred: false,
      tags: [],
    }

    if (userId === 'anonymous') {
      // Anonymous user - save to local file storage
      if (IS_LOCAL) {
        const { upsertSession } = await import('@/lib/local-session-store')
        upsertSession(userId, newSessionId, sessionData)
        console.log(`[API] Created session ${newSessionId} in local file for anonymous user`)
      } else {
        // AWS: Anonymous users don't persist sessions
        console.log(`[API] Anonymous user in AWS mode - session not persisted`)
      }
    } else {
      // Authenticated user - save to DynamoDB (AWS) or local file (local)
      if (IS_LOCAL) {
        const { upsertSession } = await import('@/lib/local-session-store')
        upsertSession(userId, newSessionId, sessionData)
        console.log(`[API] Created session ${newSessionId} in local file for user ${userId}`)
      } else {
        // AWS: Save to DynamoDB
        const { upsertSession: upsertDynamoSession } = await import('@/lib/dynamodb-client')
        await upsertDynamoSession(userId, newSessionId, sessionData)
        console.log(`[API] Created session ${newSessionId} in DynamoDB for user ${userId}`)
      }
    }

    return NextResponse.json(
      {
        success: true,
        sessionId: newSessionId,
        message: 'Session created successfully',
      },
      {
        headers: {
          'X-Session-ID': newSessionId,
        },
      }
    )
  } catch (error) {
    console.error('[API] Error creating session:', error)

    return NextResponse.json(
      {
        success: false,
        error: 'Failed to create session',
        message: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    )
  }
}
