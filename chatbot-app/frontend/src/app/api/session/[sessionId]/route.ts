/**
 * Session Management API - Update/Delete specific session
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { updateSession, deleteSession, getSession, upsertSession } from '@/lib/dynamodb-client'

// Check if running in local development mode
const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

export const runtime = 'nodejs'

/**
 * GET /api/session/[sessionId]
 * Get specific session metadata
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> }
) {
  try {
    const { sessionId } = await params
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    console.log(`[API] Getting session ${sessionId} for user ${userId}`)

    let session = null

    if (userId === 'anonymous') {
      if (IS_LOCAL) {
        const { getSession: getLocalSession } = await import('@/lib/local-session-store')
        session = getLocalSession(userId, sessionId)
      }
    } else {
      if (IS_LOCAL) {
        const { getSession: getLocalSession } = await import('@/lib/local-session-store')
        session = getLocalSession(userId, sessionId)
      } else {
        session = await getSession(userId, sessionId)
      }
    }

    if (!session) {
      return NextResponse.json(
        {
          success: false,
          error: 'Session not found',
        },
        { status: 404 }
      )
    }

    return NextResponse.json({
      success: true,
      session,
    })
  } catch (error) {
    console.error('[API] Error getting session:', error)

    return NextResponse.json(
      {
        success: false,
        error: 'Failed to get session',
      },
      { status: 500 }
    )
  }
}

/**
 * PUT /api/session/[sessionId]
 * Update session metadata (title, messageCount, lastMessageAt, etc.)
 */
export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> }
) {
  try {
    const { sessionId } = await params
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    const body = await request.json()
    const { title, messageCount, lastMessageAt, starred, tags, status } = body

    console.log(`[API] Updating session ${sessionId} for user ${userId}`, {
      title,
      messageCount,
      lastMessageAt,
      starred,
      status,
    })

    if (userId === 'anonymous') {
      if (IS_LOCAL) {
        const { updateSession: updateLocalSession } = await import('@/lib/local-session-store')
        updateLocalSession(userId, sessionId, {
          title,
          messageCount,
          lastMessageAt,
          starred,
          tags,
          status,
        })
      } else {
        return NextResponse.json(
          {
            success: false,
            error: 'Anonymous users cannot update sessions in AWS mode',
          },
          { status: 401 }
        )
      }
    } else {
      if (IS_LOCAL) {
        const { updateSession: updateLocalSession } = await import('@/lib/local-session-store')
        updateLocalSession(userId, sessionId, {
          title,
          messageCount,
          lastMessageAt,
          starred,
          tags,
          status,
        })
      } else {
        await updateSession(userId, sessionId, {
          title,
          messageCount,
          lastMessageAt,
          starred,
          tags,
          status,
        })
      }
    }

    return NextResponse.json({
      success: true,
      message: 'Session updated successfully',
    })
  } catch (error) {
    console.error('[API] Error updating session:', error)

    return NextResponse.json(
      {
        success: false,
        error: 'Failed to update session',
        message: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    )
  }
}

/**
 * POST /api/session/[sessionId]
 * Create or update session (upsert)
 */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> }
) {
  try {
    const { sessionId } = await params
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    const body = await request.json()
    const { title, messageCount, lastMessageAt, metadata } = body

    console.log(`[API] Upserting session ${sessionId} for user ${userId}`)

    if (userId === 'anonymous') {
      if (IS_LOCAL) {
        const { upsertSession: upsertLocalSession } = await import('@/lib/local-session-store')
        upsertLocalSession(userId, sessionId, {
          title,
          messageCount,
          lastMessageAt,
          metadata,
        })
      } else {
        return NextResponse.json(
          {
            success: false,
            error: 'Anonymous users cannot create sessions in AWS mode',
          },
          { status: 401 }
        )
      }
    } else {
      if (IS_LOCAL) {
        const { upsertSession: upsertLocalSession } = await import('@/lib/local-session-store')
        upsertLocalSession(userId, sessionId, {
          title,
          messageCount,
          lastMessageAt,
          metadata,
        })
      } else {
        await upsertSession(userId, sessionId, {
          title,
          messageCount,
          lastMessageAt,
          metadata,
        })
      }
    }

    return NextResponse.json({
      success: true,
      message: 'Session created/updated successfully',
    })
  } catch (error) {
    console.error('[API] Error upserting session:', error)

    return NextResponse.json(
      {
        success: false,
        error: 'Failed to create/update session',
      },
      { status: 500 }
    )
  }
}

/**
 * DELETE /api/session/[sessionId]
 * Mark session as deleted
 */
export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> }
) {
  try {
    const { sessionId } = await params
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    console.log(`[API] Deleting session ${sessionId} for user ${userId}`)

    if (userId === 'anonymous') {
      if (IS_LOCAL) {
        const { deleteSession: deleteLocalSession } = await import('@/lib/local-session-store')
        deleteLocalSession(userId, sessionId)
      } else {
        return NextResponse.json(
          {
            success: false,
            error: 'Anonymous users cannot delete sessions in AWS mode',
          },
          { status: 401 }
        )
      }
    } else {
      if (IS_LOCAL) {
        const { deleteSession: deleteLocalSession } = await import('@/lib/local-session-store')
        deleteLocalSession(userId, sessionId)
      } else {
        await deleteSession(userId, sessionId)
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
      },
      { status: 500 }
    )
  }
}
