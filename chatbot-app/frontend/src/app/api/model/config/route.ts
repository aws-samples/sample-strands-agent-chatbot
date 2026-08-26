/**
 * Model Config endpoint - returns current model configuration
 * Loads from DynamoDB (AWS) or local file (local) user preferences
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { getUserProfile } from '@/lib/dynamodb-client'
import { DEFAULT_MODEL_ID, normalizeModelId } from '@/lib/model-ids'

// Check if running in local development mode
const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

export const runtime = 'nodejs'

export async function GET(request: NextRequest) {
  try {
    // Extract user from Cognito JWT token
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    // Default configuration
    let config = {
      model_id: DEFAULT_MODEL_ID,
      temperature: 0.5,
    }

    // Load user preferences from storage (local file or DynamoDB)
    if (IS_LOCAL) {
      // Local: Use local file storage for all users
      const { getUserModelConfig } = await import('@/lib/local-tool-store')
      const savedConfig = getUserModelConfig(userId)

      if (savedConfig) {
        if (savedConfig.model_id) {
          config.model_id = normalizeModelId(savedConfig.model_id)
        }
        if (savedConfig.temperature !== undefined) {
          config.temperature = savedConfig.temperature
        }
      }

      console.log(`[API] Loaded model config for user ${userId} from local file`)
    } else {
      // AWS: Use DynamoDB for all users (including anonymous)
      try {
        const profile = await getUserProfile(userId)

        if (profile?.preferences) {
          // Override with user preferences
          if (profile.preferences.defaultModel) {
            config.model_id = normalizeModelId(profile.preferences.defaultModel)
          }
          if (profile.preferences.defaultTemperature !== undefined) {
            config.temperature = profile.preferences.defaultTemperature
          }
        }

        console.log(`[API] Loaded model config for user ${userId} from DynamoDB`)
      } catch (dbError) {
        // DynamoDB error - log and use defaults (expected for anonymous without profile)
        console.warn(`[API] Failed to load from DynamoDB for user ${userId}, using defaults:`, dbError)
      }
    }

    return NextResponse.json({
      success: true,
      config
    })
  } catch (error) {
    console.error('[API] Error loading model config:', error)

    return NextResponse.json({
      success: false,
      error: 'Failed to load model configuration'
    }, { status: 500 })
  }
}
