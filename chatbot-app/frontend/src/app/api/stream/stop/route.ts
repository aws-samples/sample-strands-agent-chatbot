/**
 * Stop Signal API endpoint
 * Sets a run-scoped stop signal to gracefully stop agent execution.
 *
 * - Local mode: POST /invocations with action="stop" (same-process provider)
 * - Cloud mode: DynamoDB PutItem (out-of-band delivery)
 *
 * Cloud mode uses DynamoDB because AgentCore Runtime does not support concurrent
 * requests on a single session — the stop invocation would queue behind the active
 * streaming request and never be delivered.
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest, getSessionId } from '@/lib/auth-utils'
import { getSession, writeStopSignal } from '@/lib/dynamodb-client'

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
    const runId = body.runId

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

    if (!runId || typeof runId !== 'string') {
      return NextResponse.json(
        { error: 'Run ID is required' },
        { status: 400 }
      )
    }

    if (!IS_LOCAL) {
      const session = await getSession(userId, sessionId)
      if (!session) {
        return NextResponse.json(
          { error: 'Session not found' },
          { status: 404 }
        )
      }
    }

    console.log(`[StopSignal] Setting stop signal for user=${userId}, session=${sessionId}, run=${runId}`)

    if (IS_LOCAL) {
      // Local mode: Call local AgentCore /invocations with AG-UI stop action
      const payload = {
        thread_id: sessionId,
        run_id: crypto.randomUUID(),
        messages: [],
        tools: [],
        context: [],
        state: { user_id: userId, action: 'stop', run_id: runId }
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
      if (result.status !== 'stop_requested') {
        console.error('[StopSignal] Local runtime did not accept stop request:', result)
        return NextResponse.json(
          { error: 'Stop signal provider is unavailable' },
          { status: 503 }
        )
      }
      console.log(`[StopSignal] Local stop signal set successfully:`, result)
    } else {
      // Cloud mode: Write stop flag to DynamoDB (out-of-band).
      // The main runtime polls DynamoDB and propagates cancellation to active tools.
      await writeStopSignal(userId, sessionId, runId)
      console.log(`[StopSignal] DynamoDB stop signal written for user=${userId}, session=${sessionId}, run=${runId}`)
    }

    return NextResponse.json({
      success: true,
      message: 'Stop signal set',
      userId,
      sessionId,
      runId,
    })

  } catch (error) {
    console.error('[StopSignal] Error:', error)
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}
