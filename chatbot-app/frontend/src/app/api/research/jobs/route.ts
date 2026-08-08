import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import {
  getResearchJob,
  getResearchJobs,
  listResearchJobs,
} from '@/lib/research-jobs'

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
    const jobId = request.nextUrl.searchParams.get('job_id')
    const jobIds = request.nextUrl.searchParams.get('job_ids')
    if (jobId) {
      const job = await getResearchJob(user.userId, sessionId, jobId, {
        includeContent,
      })
      if (!job) {
        return NextResponse.json({ error: 'Research job not found' }, { status: 404 })
      }
      return NextResponse.json({ job })
    }
    if (jobIds) {
      const requestedIds = Array.from(new Set(
        jobIds.split(',').map(value => value.trim()).filter(Boolean),
      ))
      if (requestedIds.length > 100) {
        return NextResponse.json(
          { error: 'At most 100 job_ids may be requested' },
          { status: 400 },
        )
      }
      const jobs = await getResearchJobs(
        user.userId,
        sessionId,
        requestedIds,
        { includeContent },
      )
      return NextResponse.json({ jobs })
    }
    const jobs = await listResearchJobs(user.userId, sessionId, { includeContent })
    return NextResponse.json({ jobs })
  } catch (error) {
    console.error('[ResearchJobs] Failed to list jobs:', error)
    return NextResponse.json({ error: 'Failed to list research jobs' }, { status: 500 })
  }
}
