import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import {
  cancelDelegationJob,
  listDelegationJobs,
} from '@/lib/delegation-jobs'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function GET(request: NextRequest) {
  try {
    const sessionId = request.nextUrl.searchParams.get('session_id')
    if (!sessionId) {
      return NextResponse.json(
        { error: 'Missing session_id parameter' },
        { status: 400 },
      )
    }
    const user = await extractUserFromRequest(request)
    const jobs = await listDelegationJobs(user.userId, sessionId)
    return NextResponse.json({ jobs })
  } catch (error) {
    console.error('[Delegations] Failed to list jobs:', error)
    return NextResponse.json(
      { error: 'Failed to list delegations' },
      { status: 500 },
    )
  }
}

export async function DELETE(request: NextRequest) {
  try {
    const body = await request.json()
    const sessionId = typeof body?.session_id === 'string'
      ? body.session_id
      : ''
    const jobId = typeof body?.job_id === 'string' ? body.job_id : ''
    if (!sessionId || !jobId) {
      return NextResponse.json(
        { error: 'session_id and job_id are required' },
        { status: 400 },
      )
    }
    const user = await extractUserFromRequest(request)
    const job = await cancelDelegationJob(user.userId, sessionId, jobId)
    if (!job) {
      return NextResponse.json(
        { error: 'Delegation not found' },
        { status: 404 },
      )
    }
    return NextResponse.json({ job })
  } catch (error) {
    console.error('[Delegations] Failed to cancel job:', error)
    return NextResponse.json(
      { error: 'Failed to cancel delegation' },
      { status: 500 },
    )
  }
}
