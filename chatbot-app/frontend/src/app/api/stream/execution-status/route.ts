/**
 * Execution status endpoint (BFF)
 * Proxies to backend ExecutionRegistry via agentcore-runtime-client.
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { getExecutionStatus } from '@/lib/agentcore-runtime-client'

export async function GET(request: NextRequest) {
  const executionId = request.nextUrl.searchParams.get('executionId')

  if (!executionId) {
    return NextResponse.json({ error: 'executionId is required' }, { status: 400 })
  }

  const user = await extractUserFromRequest(request)
  const authToken = request.headers.get('authorization') || ''

  const result = await getExecutionStatus(executionId, user.userId, authToken)
  return NextResponse.json(result)
}
