/**
 * Update message metadata in session
 * Stores per-message metadata (latency, sentiment, etc.) indexed by message ID
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

export const runtime = 'nodejs'

export async function POST(request: NextRequest) {
  try {
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    const { sessionId, messageId, metadata } = await request.json()

    if (!sessionId || !messageId || !metadata) {
      return NextResponse.json(
        { success: false, error: 'sessionId, messageId, and metadata required' },
        { status: 400 }
      )
    }

    console.log(`[API] Updating metadata for message ${messageId} in session ${sessionId}:`, JSON.stringify(metadata))

    if (IS_LOCAL) {
      const { updateSession, getSession } = await import('@/lib/local-session-store')
      const session = getSession(userId, sessionId)
      console.log(`[API] LOCAL - Session found: ${!!session}`)
      if (session) {
        const existingMessages = session.metadata?.messages || {}
        const existingMessageData = existingMessages[messageId] || {}
        console.log(`[API] LOCAL - Existing messages: ${Object.keys(existingMessages).length}, updating message: ${messageId}`)

        updateSession(userId, sessionId, {
          metadata: {
            ...session.metadata,
            messages: {
              ...existingMessages,
              [messageId]: {
                ...existingMessageData,
                ...metadata
              }
            }
          }
        })
        console.log(`[API] LOCAL - Metadata updated successfully`)
      } else {
        console.warn(`[API] LOCAL - Session not found: ${sessionId}`)
      }
    } else {
      const {
        messageMetadataEnabled,
        updateMessageMetadataRecord,
      } = await import('@/lib/message-metadata')
      if (messageMetadataEnabled()) {
        await updateMessageMetadataRecord(
          userId,
          sessionId,
          messageId,
          metadata,
        )
        return NextResponse.json({ success: true })
      }

      const { updateSession, getSession } = await import('@/lib/dynamodb-client')
      const session = await getSession(userId, sessionId)
      console.log(`[API] Session found: ${!!session}, metadata: ${JSON.stringify(session?.metadata || null)}`)
      if (session) {
        const existingMessages = session.metadata?.messages || {}
        const existingMessageData = existingMessages[messageId] || {}

        const newMetadata = {
          ...session.metadata,
          messages: {
            ...existingMessages,
            [messageId]: {
              ...existingMessageData,
              ...metadata
            }
          }
        }
        console.log(`[API] Updating with metadata: ${JSON.stringify(newMetadata)}`)

        await updateSession(userId, sessionId, {
          metadata: newMetadata
        })
        console.log(`[API] Metadata update completed for message ${messageId}`)
      } else {
        console.warn(`[API] Session not found for ${sessionId}, metadata not saved`)
      }
    }

    return NextResponse.json({ success: true })
  } catch (error) {
    console.error('[API] Error updating message metadata:', error)
    return NextResponse.json(
      {
        success: false,
        error: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    )
  }
}
