import { NextRequest, NextResponse } from 'next/server'
import { pingAgentCoreRuntime } from '@/lib/agentcore-runtime-client'
import { extractUserFromRequest } from '@/lib/auth-utils'

export async function POST(request: NextRequest) {
  const startTime = Date.now()
  console.log('[Warmup API] Request received')

  try {
    const body = await request.json().catch(() => ({}))
    const { userId } = await extractUserFromRequest(request)
    const sessionId = body.sessionId

    const authHeader = request.headers.get('authorization') || ''
    const authToken = authHeader.startsWith('Bearer ') ? authHeader : ''

    console.log(`[Warmup API] Params: userId=${userId || 'none'}, sessionId=${sessionId || 'none'}`)

    const result = await pingAgentCoreRuntime(sessionId, userId, authToken)
    const totalMs = Date.now() - startTime

    if (result.success) {
      console.log(`[Warmup API] Success: ${result.latencyMs}ms (mode=${result.mode}, total=${totalMs}ms)`)
    } else {
      console.error(`[Warmup API] Failed: ${result.error} (mode=${result.mode}, total=${totalMs}ms)`)
    }

    return NextResponse.json(result)
  } catch (error) {
    const totalMs = Date.now() - startTime
    const errorMsg = error instanceof Error ? error.message : 'Unknown error'
    console.error(`[Warmup API] Error: ${errorMsg} (total=${totalMs}ms)`)

    return NextResponse.json(
      { success: false, error: errorMsg },
      { status: 500 }
    )
  }
}
