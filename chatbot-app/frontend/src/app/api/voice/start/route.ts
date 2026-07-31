/**
 * Voice Session Start API - returns WebSocket URL for voice chat
 * Local: Direct WS to AgentCore
 * Cloud: JWT-authenticated WebSocket URL to AgentCore Runtime
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest, getSessionId, ensureSessionExists } from '@/lib/auth-utils'

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

const AWS_REGION = process.env.AWS_REGION || 'us-west-2'
const PROJECT_NAME = process.env.PROJECT_NAME || 'strands-agent-chatbot'
const ENVIRONMENT = process.env.ENVIRONMENT || 'dev'

let cachedRuntimeArn: string | null = null
let cacheExpiry: number = 0
const CACHE_TTL_MS = 5 * 60 * 1000

async function getRuntimeArnFromSSM(): Promise<string | null> {
  if (cachedRuntimeArn && Date.now() < cacheExpiry) {
    return cachedRuntimeArn
  }

  const parameterName = `/${PROJECT_NAME}/${ENVIRONMENT}/agentcore/runtime-arn`

  try {
    const { SSMClient, GetParameterCommand } = await import('@aws-sdk/client-ssm')
    const client = new SSMClient({ region: AWS_REGION })

    const response = await client.send(new GetParameterCommand({ Name: parameterName }))

    if (response.Parameter?.Value) {
      cachedRuntimeArn = response.Parameter.Value
      cacheExpiry = Date.now() + CACHE_TTL_MS
      console.log(`[Voice Start] Loaded Runtime ARN from SSM: ${cachedRuntimeArn}`)
      return cachedRuntimeArn
    }
  } catch (error) {
    console.warn(`[Voice Start] Failed to get Runtime ARN from SSM (${parameterName}):`, error)
  }

  return null
}

function buildWsUrl(runtimeArn: string, region: string, queryParams: Record<string, string>): string {
  const host = `bedrock-agentcore.${region}.amazonaws.com`
  const encodedArn = encodeURIComponent(runtimeArn)
  const url = new URL(`wss://${host}/runtimes/${encodedArn}/ws`)

  for (const [key, value] of Object.entries(queryParams)) {
    url.searchParams.set(key, value)
  }

  return url.toString()
}

export const runtime = 'nodejs'

export async function POST(request: NextRequest) {
  try {
    const user = await extractUserFromRequest(request)
    const userId = user.userId
    const { sessionId } = getSessionId(request, userId)
    const body = await request.json().catch(() => ({}))
    const enabledTools: string[] = body.enabledTools || []

    const { isNew } = await ensureSessionExists(userId, sessionId, {
      title: 'Voice Chat',
      metadata: { isVoiceSession: true },
    })

    console.log(`[Voice Start] User: ${userId}, Session: ${sessionId}, New: ${isNew}, Tools: ${enabledTools.length}`)

    let wsUrl: string

    if (IS_LOCAL) {
      const agentcoreUrl = process.env.NEXT_PUBLIC_AGENTCORE_URL || 'http://localhost:8080'
      wsUrl = agentcoreUrl.replace('http://', 'ws://').replace('https://', 'wss://') + '/voice/stream'
      const params = new URLSearchParams()
      params.set('session_id', sessionId)
      if (userId) params.set('user_id', userId)
      if (enabledTools.length > 0) params.set('enabled_tools', JSON.stringify(enabledTools))
      wsUrl = `${wsUrl}?${params.toString()}`
    } else {
      const runtimeArn = await getRuntimeArnFromSSM()
      if (!runtimeArn) {
        return NextResponse.json({ success: false, error: 'AgentCore Runtime not configured' }, { status: 500 })
      }

      const queryParams: Record<string, string> = {
        'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': sessionId,
      }
      if (userId) {
        queryParams['X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id'] = userId
      }
      if (enabledTools.length > 0) {
        queryParams['X-Amzn-Bedrock-AgentCore-Runtime-Custom-Enabled-Tools'] = JSON.stringify(enabledTools)
      }
      wsUrl = buildWsUrl(runtimeArn, AWS_REGION, queryParams)
    }

    console.log(`[Voice Start] WebSocket URL generated (${IS_LOCAL ? 'local' : 'cloud'} mode)`)

    return NextResponse.json({
      success: true,
      sessionId,
      userId,
      wsUrl,
      awsRegion: AWS_REGION,
      isNewSession: isNew,
      isLocal: IS_LOCAL,
    })
  } catch (error) {
    console.error('[Voice Start] Error:', error)
    return NextResponse.json(
      {
        success: false,
        error: error instanceof Error ? error.message : 'Failed to start voice session',
      },
      { status: 500 }
    )
  }
}
