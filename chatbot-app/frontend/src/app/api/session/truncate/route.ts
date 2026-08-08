/**
 * Session Truncate - Delete conversation events from a given point onward (inclusive)
 *
 * POST { sessionId, fromEventId? , fromTimestamp? }
 *
 * Cloud mode:
 *   - fromEventId (preferred): list events in order, find fromEventId, delete it and everything after
 *   - fromTimestamp (fallback for newly-sent messages): delete events with eventTime >= fromTimestamp
 * Local mode: deletes message_*.json files whose created_at >= fromTimestamp
 *
 * History messages already carry their eventId as message.id (set by the history route).
 * Newly-sent messages in the current session use Date.now() as id and fall back to fromTimestamp.
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'
const AWS_REGION = process.env.AWS_REGION || process.env.NEXT_PUBLIC_AWS_REGION || 'us-west-2'
const PROJECT_NAME = process.env.PROJECT_NAME || 'strands-agent-chatbot'
const ENVIRONMENT = process.env.ENVIRONMENT || 'dev'

export const runtime = 'nodejs'

async function getMemoryId(): Promise<string | null> {
  const envMemoryId = process.env.MEMORY_ID || process.env.NEXT_PUBLIC_MEMORY_ID
  if (envMemoryId) return envMemoryId

  try {
    const { SSMClient, GetParameterCommand } = await import('@aws-sdk/client-ssm')
    const ssmClient = new SSMClient({ region: AWS_REGION })
    const paramPath = `/${PROJECT_NAME}/${ENVIRONMENT}/agentcore/memory-id`
    const response = await ssmClient.send(new GetParameterCommand({ Name: paramPath }))
    return response.Parameter?.Value ?? null
  } catch {
    return null
  }
}

export async function POST(request: NextRequest) {
  try {
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    const {
      sessionId,
      fromEventId,
      fromTimestamp,
    } = await request.json()
    if (!sessionId) {
      return NextResponse.json({ success: false, error: 'sessionId is required' }, { status: 400 })
    }
    if (!fromEventId && typeof fromTimestamp !== 'number') {
      return NextResponse.json(
        { success: false, error: 'fromEventId or fromTimestamp is required' },
        { status: 400 }
      )
    }

    console.log(`[truncate] Session ${sessionId}: fromEventId=${fromEventId ?? 'none'}, fromTimestamp=${fromTimestamp ?? 'none'}`)

    if (userId === 'anonymous' || IS_LOCAL) {
      if (typeof fromTimestamp === 'number') {
        if (!Number.isFinite(fromTimestamp)) {
          return NextResponse.json(
            { success: false, error: 'fromTimestamp must be finite' },
            { status: 400 },
          )
        }
        const { advanceSessionConversationEpoch } =
          await import('@/lib/session-lifecycle')
        const conversationEpoch = await advanceSessionConversationEpoch(
          userId,
          sessionId,
          new Date(fromTimestamp).toISOString(),
        )
        const { truncateSessionMessages } = await import('@/lib/local-session-store')
        const deleted = truncateSessionMessages(userId, sessionId, fromTimestamp)
        console.log(`[truncate] LOCAL - Deleted ${deleted} messages`)
        return NextResponse.json({
          success: true,
          sessionId,
          deleted,
          conversationEpoch,
        })
      }
      // Local mode has no eventId concept — nothing to do
      return NextResponse.json({ success: true, sessionId, deleted: 0 })
    }

    const memoryId = await getMemoryId()
    if (!memoryId) {
      return NextResponse.json({ success: false, error: 'Memory ID not available' }, { status: 500 })
    }

    const { BedrockAgentCoreClient, ListEventsCommand, DeleteEventCommand } =
      await import('@aws-sdk/client-bedrock-agentcore')

    const client = new BedrockAgentCoreClient({ region: AWS_REGION })

    // Collect all events in order (API returns newest-first → reverse for chronological)
    const allEventPages: any[] = []
    let nextToken: string | undefined
    do {
      const response = await client.send(new ListEventsCommand({
        memoryId,
        sessionId,
        actorId: userId,
        includePayloads: false,
        maxResults: 100,
        nextToken,
      }))
      allEventPages.push(...(response.events || []))
      nextToken = response.nextToken
    } while (nextToken)

    // Reverse to chronological order (oldest first)
    const chronologicalEvents = [...allEventPages].reverse()

    console.log(`[truncate] Listed ${chronologicalEvents.length} events total`)

    let toDelete: { eventId: string; actorId: string }[] = []
    let cutoffMs: number

    if (fromEventId) {
      // Positional approach: find the event and delete it + everything after
      const fromIndex = chronologicalEvents.findIndex((e: any) => e.eventId === fromEventId)
      if (fromIndex >= 0) {
        const cutoffEventTime = chronologicalEvents[fromIndex]?.eventTime
        cutoffMs = cutoffEventTime ? new Date(cutoffEventTime).getTime() : NaN
        if (!Number.isFinite(cutoffMs)) {
          return NextResponse.json(
            { success: false, error: 'Target event has no usable eventTime' },
            { status: 409 },
          )
        }
        toDelete = chronologicalEvents
          .slice(fromIndex)
          .filter((e: any) => e.eventId)
          .map((e: any) => ({ eventId: e.eventId, actorId: userId }))
        console.log(`[truncate] fromEventId found at index ${fromIndex}, deleting ${toDelete.length} events`)
      } else {
        return NextResponse.json(
          { success: false, error: `Event ${fromEventId} was not found` },
          { status: 404 },
        )
      }
    } else {
      cutoffMs = fromTimestamp
      if (!Number.isFinite(cutoffMs)) {
        return NextResponse.json(
          { success: false, error: 'fromTimestamp must be finite' },
          { status: 400 },
        )
      }
      // Timestamp fallback: delete events with eventTime >= fromTimestamp
      for (const event of chronologicalEvents) {
        if (!event.eventId) continue
        const eventMs = (event as any).eventTime ? new Date((event as any).eventTime).getTime() : NaN
        if (!isNaN(eventMs) && eventMs >= fromTimestamp) {
          toDelete.push({ eventId: event.eventId, actorId: userId })
        }
      }
      console.log(`[truncate] Timestamp approach: ${toDelete.length} events >= ${fromTimestamp}`)
    }

    const { advanceSessionConversationEpoch } =
      await import('@/lib/session-lifecycle')
    const conversationEpoch = await advanceSessionConversationEpoch(
      userId,
      sessionId,
      new Date(cutoffMs).toISOString(),
    )

    const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

    const deleteWithRetry = async (eventId: string, actorId: string, retries = 3): Promise<void> => {
      for (let attempt = 0; attempt < retries; attempt++) {
        try {
          await client.send(new DeleteEventCommand({ memoryId, sessionId, eventId, actorId }))
          return
        } catch (err: any) {
          if (err?.name === 'ResourceNotFoundException' || err?.$metadata?.httpStatusCode === 404) {
            return
          }
          const isRateError = err?.message?.includes('Rate exceeded') ||
            err?.name === 'ThrottlingException' ||
            err?.name === 'TooManyRequestsException'
          if (isRateError && attempt < retries - 1) {
            await sleep(500 * (attempt + 1))
          } else {
            throw err
          }
        }
      }
    }

    const BATCH_SIZE = 10
    for (let i = 0; i < toDelete.length; i += BATCH_SIZE) {
      const batch = toDelete.slice(i, i + BATCH_SIZE)
      await Promise.all(batch.map(({ eventId, actorId }) => deleteWithRetry(eventId, actorId)))
      if (i + BATCH_SIZE < toDelete.length) {
        await sleep(500)
      }
    }

    console.log(`[truncate] Deleted ${toDelete.length} events from session ${sessionId}`)
    return NextResponse.json({
      success: true,
      sessionId,
      deleted: toDelete.length,
      conversationEpoch,
    })
  } catch (error) {
    console.error('[truncate] Error:', error)
    return NextResponse.json(
      {
        success: false,
        error: 'Failed to truncate session',
        message: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    )
  }
}
