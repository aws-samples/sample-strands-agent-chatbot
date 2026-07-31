import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { getUserDisabledSkills, updateUserDisabledSkills } from '@/lib/dynamodb-client'

export const runtime = 'nodejs'

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

export async function GET(request: NextRequest) {
  try {
    const user = await extractUserFromRequest(request)

    if (IS_LOCAL) {
      return NextResponse.json({ disabledSkills: [] })
    }

    const disabledSkills = await getUserDisabledSkills(user.userId)
    return NextResponse.json({ disabledSkills })
  } catch (error) {
    console.error('[API] Error getting disabled skills:', error)
    return NextResponse.json({ disabledSkills: [] })
  }
}

export async function PUT(request: NextRequest) {
  try {
    const user = await extractUserFromRequest(request)
    const body = await request.json()
    const disabledSkills: string[] = Array.isArray(body.disabledSkills) ? body.disabledSkills : []

    if (IS_LOCAL) {
      return NextResponse.json({ success: true })
    }

    await updateUserDisabledSkills(user.userId, disabledSkills)
    return NextResponse.json({ success: true })
  } catch (error) {
    console.error('[API] Error updating disabled skills:', error)
    return NextResponse.json({ success: false, error: 'Failed to update' }, { status: 500 })
  }
}
