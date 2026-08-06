import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { listResearchJobs } from '@/lib/research-jobs'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function GET(request: NextRequest) {
  try {
    const sessionId = request.nextUrl.searchParams.get('session_id')
    if (!sessionId) {
      return NextResponse.json({ error: 'Missing session_id parameter' }, { status: 400 })
    }

    const user = await extractUserFromRequest(request)
    const includeContent = request.nextUrl.searchParams.get('include_content') === 'true'
    const jobs = await listResearchJobs(user.userId, sessionId, { includeContent })
    return NextResponse.json({ jobs })
  } catch (error) {
    console.error('[ResearchJobs] Failed to list jobs:', error)
    return NextResponse.json({ error: 'Failed to list research jobs' }, { status: 500 })
  }
}
