/**
 * Session Compact - Delete conversation events from the current session
 *
 * GET  ?sessionId=xxx  → Returns current eventIds (captured before sending summary)
 * POST { sessionId, eventIds? } → Deletes the specified eventIds (or all if not provided)
 *
 * Keeps the same session ID so stateful tools remain intact.
 * Caller should send the summary message BEFORE calling POST to ensure
 * the summary event exists in AgentCore Memory before old events are deleted.
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

/** List all eventIds for the session (used to capture snapshot before sending summary) */
export async function GET(request: NextRequest) {
  try {
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    const { searchParams } = new URL(request.url)
    const sessionId = searchParams.get('sessionId')
    if (!sessionId) {
      return NextResponse.json({ success: false, error: 'sessionId is required' }, { status: 400 })
    }

    if (userId === 'anonymous' || IS_LOCAL) {
      // Return current message filenames as pseudo event IDs so POST can selectively delete them
      const { getSessionMessageFileIds } = await import('@/lib/local-session-store')
      const eventIds = getSessionMessageFileIds(sessionId)
      return NextResponse.json({ success: true, eventIds })
    }

    const memoryId = await getMemoryId()
    if (!memoryId) {
      return NextResponse.json({ success: false, error: 'Memory ID not available' }, { status: 500 })
    }

    const { BedrockAgentCoreClient, ListEventsCommand } = await import('@aws-sdk/client-bedrock-agentcore')
    const client = new BedrockAgentCoreClient({ region: AWS_REGION })

    const eventIds: string[] = []
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
      for (const event of response.events || []) {
        if (event.eventId) eventIds.push(event.eventId)
      }
      nextToken = response.nextToken
    } while (nextToken)

    console.log(`[compact/list] Found ${eventIds.length} events for session ${sessionId}`)
    return NextResponse.json({ success: true, eventIds })
  } catch (error) {
    console.error('[compact/list] Error:', error)
    return NextResponse.json(
      { success: false, error: 'Failed to list events', message: error instanceof Error ? error.message : 'Unknown error' },
      { status: 500 }
    )
  }
}

/** Delete the specified eventIds (or all events if eventIds not provided) */
export async function POST(request: NextRequest) {
  try {
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    const { sessionId, eventIds } = await request.json()
    if (!sessionId) {
      return NextResponse.json(
        { success: false, error: 'sessionId is required' },
        { status: 400 }
      )
    }

    console.log(`[compact] Clearing events for session ${sessionId}, user ${userId}`)

    if (userId === 'anonymous' || IS_LOCAL) {
      if (Array.isArray(eventIds) && eventIds.length > 0) {
        // Delete only the specific files captured before the summary was sent
        const { deleteSessionMessageFiles } = await import('@/lib/local-session-store')
        deleteSessionMessageFiles(sessionId, eventIds)
        console.log(`[compact] LOCAL - Deleted ${eventIds.length} message files for session ${sessionId}`)
      } else if (!Array.isArray(eventIds)) {
        // No snapshot provided — delete all (legacy / fallback)
        const { clearSessionMessages } = await import('@/lib/local-session-store')
        clearSessionMessages(userId, sessionId)
        console.log(`[compact] LOCAL - Messages cleared for session ${sessionId}`)
      }
    } else {
      const memoryId = await getMemoryId()
      if (!memoryId) {
        return NextResponse.json(
          { success: false, error: 'Memory ID not available' },
          { status: 500 }
        )
      }

      const { BedrockAgentCoreClient, ListEventsCommand, DeleteEventCommand } =
        await import('@aws-sdk/client-bedrock-agentcore')

      const client = new BedrockAgentCoreClient({ region: AWS_REGION })

      // Use provided snapshot if given (empty array = delete nothing); list all only when absent.
      let allEvents: { eventId: string; actorId: string }[]
      if (Array.isArray(eventIds)) {
        allEvents = eventIds.map((id: string) => ({ eventId: id, actorId: userId }))
        console.log(`[compact] Using provided ${allEvents.length} eventIds`)
      } else {
        allEvents = []
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
          for (const event of response.events || []) {
            if (event.eventId) {
              allEvents.push({ eventId: event.eventId, actorId: userId })
            }
          }
          nextToken = response.nextToken
        } while (nextToken)
        console.log(`[compact] Listed ${allEvents.length} events to delete`)
      }

      const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

      const deleteWithRetry = async (eventId: string, actorId: string, retries = 6): Promise<void> => {
        for (let attempt = 0; attempt < retries; attempt++) {
          try {
            await client.send(new DeleteEventCommand({ memoryId, sessionId, eventId, actorId }))
            return
          } catch (err: any) {
            // Already deleted — treat as success (idempotent for retry after refresh)
            if (err?.name === 'ResourceNotFoundException' || err?.$metadata?.httpStatusCode === 404) {
              return
            }
            const isRateError = err?.message?.includes('Rate exceeded') ||
              err?.name === 'ThrottlingException' ||
              err?.name === 'TooManyRequestsException'
            if (isRateError && attempt < retries - 1) {
              const base = 1000 * Math.pow(2, attempt) // exponential backoff with jitter
              const jitter = Math.floor(Math.random() * 500)
              await sleep(base + jitter)
            } else {
              throw err
            }
          }
        }
      }

      // Sequential batches of 5, 1s inter-batch delay (~5 TPS)
      const BATCH_SIZE = 5
      for (let i = 0; i < allEvents.length; i += BATCH_SIZE) {
        const batch = allEvents.slice(i, i + BATCH_SIZE)
        for (const { eventId, actorId } of batch) {
          await deleteWithRetry(eventId, actorId)
        }
        if (i + BATCH_SIZE < allEvents.length) {
          await sleep(1000)
        }
      }

      console.log(`[compact] Deleted ${allEvents.length} events from session ${sessionId}`)
    }

    return NextResponse.json({ success: true, sessionId })
  } catch (error) {
    console.error('[compact] Error:', error)
    return NextResponse.json(
      {
        success: false,
        error: 'Failed to clear session events',
        message: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    )
  }
}
