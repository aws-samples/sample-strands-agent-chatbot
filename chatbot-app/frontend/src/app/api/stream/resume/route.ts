/**
 * Resume SSE stream endpoint (BFF)
 * Proxies to backend ExecutionRegistry for cursor-based event replay.
 */
import { NextRequest } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { resumeExecution } from '@/lib/agentcore-runtime-client'

export const runtime = 'nodejs'

export async function GET(request: NextRequest) {
  const executionId = request.nextUrl.searchParams.get('executionId')
  const cursor = parseInt(request.nextUrl.searchParams.get('cursor') || '0', 10)

  if (!executionId) {
    return new Response(
      JSON.stringify({ error: 'executionId is required' }),
      { status: 400, headers: { 'Content-Type': 'application/json' } }
    )
  }

  const user = await extractUserFromRequest(request)
  const authToken = request.headers.get('authorization') || ''

  console.log(`[Resume] Proxying to backend: execution=${executionId}, cursor=${cursor}`)

  try {
    const backendStream = await resumeExecution(
      executionId, cursor, user.userId, authToken, request.signal
    )

    return new Response(backendStream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache, no-transform',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive',
      },
    })
  } catch (error) {
    console.error('[Resume] Backend resume failed:', error)
    return new Response(
      JSON.stringify({ error: 'Execution not found or expired' }),
      { status: 404, headers: { 'Content-Type': 'application/json' } }
    )
  }
}
