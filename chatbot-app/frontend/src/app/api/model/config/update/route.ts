/**
 * Update Model Config endpoint - saves model configuration to DynamoDB (AWS) or local file (local)
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { getUserProfile, upsertUserProfile } from '@/lib/dynamodb-client'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// Check if running in local development mode
const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

export async function POST(request: NextRequest) {
  try {
    // Extract user from Cognito JWT token
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    const body = await request.json()
    const { model_id, temperature } = body

    if (userId === 'anonymous') {
      // Anonymous user - save to local file storage (works for both local and AWS)
      const { updateUserModelConfig } = await import('@/lib/local-tool-store')
      updateUserModelConfig(userId, {
        model_id,
        temperature
      })
      console.log(`[API] Updated model config for anonymous user via local file: ${model_id}, temp: ${temperature}`)

      return NextResponse.json({
        success: true,
        message: 'Model configuration updated successfully (local storage)'
      })
    }

    // Authenticated user - save to DynamoDB (AWS) or local file (local)
    if (IS_LOCAL) {
      // Local: Save to file
      const { updateUserModelConfig } = await import('@/lib/local-tool-store')
      updateUserModelConfig(userId, {
        model_id,
        temperature
      })
      console.log(`[API] Updated model config for user ${userId} via local file: ${model_id}, temp: ${temperature}`)
    } else {
      // AWS: Save to DynamoDB
      const profile = await getUserProfile(userId)

      await upsertUserProfile(userId, user.email || '', user.username, {
        ...(profile?.preferences || {}),
        defaultModel: model_id,
        defaultTemperature: temperature
      })

      console.log(`[API] Updated model config for user ${userId} via DynamoDB: ${model_id}, temp: ${temperature}`)
    }

    return NextResponse.json({
      success: true,
      message: 'Model configuration updated successfully'
    })
  } catch (error) {
    console.error('[API] Error updating model config:', error)

    return NextResponse.json({
      success: false,
      error: 'Failed to update model configuration'
    }, { status: 500 })
  }
}
