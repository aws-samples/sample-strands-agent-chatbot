/**
 * Stop Signal API endpoint
 * Sets stop signal for a specific user-session to gracefully stop streaming.
 *
 * - Local mode: POST /invocations with action="stop" (in-memory flag, same process)
 * - Cloud mode: DynamoDB PutItem (out-of-band, agents poll independently)
 *
 * Cloud mode uses DynamoDB because AgentCore Runtime does not support concurrent
 * requests on a single session — the stop invocation would queue behind the active
 * streaming request and never be delivered.
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest, getSessionId } from '@/lib/auth-utils'
import { writeStopSignal } from '@/lib/dynamodb-client'

// Check if running in local mode
const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'
const AGENTCORE_URL = process.env.NEXT_PUBLIC_AGENTCORE_URL || 'http://localhost:8080'

export async function POST(request: NextRequest) {
  try {
    // Extract user from request
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    // Get session ID from request body or header
    const body = await request.json().catch(() => ({}))
    let sessionId = body.sessionId

    // Fallback to header if not in body
    if (!sessionId) {
      const { sessionId: headerSessionId } = getSessionId(request, userId)
      sessionId = headerSessionId
    }

    if (!sessionId) {
      return NextResponse.json(
        { error: 'Session ID is required' },
        { status: 400 }
      )
    }

    console.log(`[StopSignal] Setting stop signal for user=${userId}, session=${sessionId}`)

    if (IS_LOCAL) {
      // Local mode: Call local AgentCore /invocations with AG-UI stop action
      const payload = {
        thread_id: sessionId,
        run_id: crypto.randomUUID(),
        messages: [],
        tools: [],
        context: [],
        state: { user_id: userId, action: 'stop' }
      }
      const response = await fetch(`${AGENTCORE_URL}/invocations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })

      if (!response.ok) {
        const errorText = await response.text()
        console.error(`[StopSignal] Local AgentCore error: ${errorText}`)
        return NextResponse.json(
          { error: 'Failed to set stop signal' },
          { status: 500 }
        )
      }

      const result = await response.json()
      console.log(`[StopSignal] Local stop signal set successfully:`, result)
    } else {
      // Cloud mode: Write stop flag to DynamoDB (out-of-band).
      // Agents poll DynamoDB independently to detect stop signals.
      await writeStopSignal(userId, sessionId)
      console.log(`[StopSignal] DynamoDB stop signal written for user=${userId}, session=${sessionId}`)
    }

    return NextResponse.json({
      success: true,
      message: 'Stop signal set',
      userId,
      sessionId
    })

  } catch (error) {
    console.error('[StopSignal] Error:', error)
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}
