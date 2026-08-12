/**
 * AgentCore Client - Supports both local and AWS deployment
 * - Local: HTTP POST to localhost:8080
 * - AWS: HTTP POST to AgentCore Runtime invocation URL with JWT auth
 */

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'
const AGENTCORE_URL = process.env.NEXT_PUBLIC_AGENTCORE_URL || 'http://localhost:8080'

const AWS_REGION = process.env.AWS_REGION || 'us-west-2'
const PROJECT_NAME = process.env.PROJECT_NAME || 'strands-agent-chatbot'
const ENVIRONMENT = process.env.ENVIRONMENT || 'dev'

let SSMClient: any
let GetParameterCommand: any
let ssmClient: any
let cachedRuntimeUrl: string | null = null

async function initializeSsmClient() {
  if (!SSMClient) {
    const ssmModule = await import('@aws-sdk/client-ssm')
    SSMClient = ssmModule.SSMClient
    GetParameterCommand = ssmModule.GetParameterCommand
    ssmClient = new SSMClient({ region: AWS_REGION })
  }
}

async function getAgentCoreRuntimeUrl(): Promise<string> {
  if (cachedRuntimeUrl) {
    return cachedRuntimeUrl
  }

  const envUrl = process.env.AGENTCORE_RUNTIME_URL
  if (envUrl) {
    console.log('[AgentCore] Using AGENTCORE_RUNTIME_URL from environment')
    cachedRuntimeUrl = envUrl
    return envUrl
  }

  // Fall back: build URL from ARN env var
  const envArn = process.env.AGENTCORE_RUNTIME_ARN
  if (envArn) {
    const url = `https://bedrock-agentcore.${AWS_REGION}.amazonaws.com/runtimes/${encodeURIComponent(envArn)}/invocations?qualifier=DEFAULT`
    console.log('[AgentCore] Built runtime URL from AGENTCORE_RUNTIME_ARN')
    cachedRuntimeUrl = url
    return url
  }

  // Fall back: SSM Parameter Store
  try {
    await initializeSsmClient()
    const paramPath = `/${PROJECT_NAME}/${ENVIRONMENT}/agentcore/runtime-url`
    console.log(`[AgentCore] Loading Runtime URL from Parameter Store: ${paramPath}`)

    const command = new GetParameterCommand({ Name: paramPath })
    const response = await ssmClient.send(command)

    if (response.Parameter?.Value) {
      console.log('[AgentCore] Runtime URL loaded from Parameter Store')
      cachedRuntimeUrl = response.Parameter.Value
      return response.Parameter.Value
    }
  } catch (error) {
    console.warn('[AgentCore] Failed to load from Parameter Store:', error)
  }

  throw new Error(
    'AGENTCORE_RUNTIME_URL not configured. Set the environment variable or Parameter Store value.'
  )
}

async function invokeLocalAgentCore(
  aguiBody: Record<string, any>,
  abortSignal?: AbortSignal
): Promise<ReadableStream> {
  console.log('[AgentCore] Invoking LOCAL AgentCore via HTTP POST')
  console.log(`[AgentCore]    URL: ${AGENTCORE_URL}/invocations`)

  const response = await fetch(`${AGENTCORE_URL}/invocations`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
    },
    body: JSON.stringify(aguiBody),
    signal: abortSignal,
  })

  if (!response.ok) {
    const errorText = await response.text()
    throw new Error(`AgentCore returned ${response.status}: ${errorText}`)
  }

  console.log('[AgentCore] Local Runtime invoked successfully')

  if (!response.body) {
    throw new Error('No response stream received from AgentCore')
  }

  return response.body
}

async function invokeAwsAgentCore(
  aguiBody: Record<string, any>,
  userId: string,
  sessionId: string,
  authToken: string,
  abortSignal?: AbortSignal
): Promise<ReadableStream> {
  const runtimeUrl = await getAgentCoreRuntimeUrl()

  console.log('[AgentCore] Invoking AWS Bedrock AgentCore Runtime via fetch')
  console.log(`[AgentCore]    User: ${userId}, Session: ${sessionId}`)

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Accept': 'text/event-stream',
    'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': sessionId,
  }

  if (authToken) {
    headers['Authorization'] = authToken
  }

  const response = await fetch(runtimeUrl, {
    method: 'POST',
    headers,
    body: JSON.stringify(aguiBody),
    signal: abortSignal,
  })

  if (!response.ok) {
    const errorText = await response.text()
    throw new Error(`AgentCore Runtime returned ${response.status}: ${errorText}`)
  }

  console.log('[AgentCore] AWS Runtime invoked successfully')

  if (!response.body) {
    throw new Error('No response stream received from AgentCore Runtime')
  }

  return response.body
}


/**
 * Invoke AgentCore and stream the response.
 * Accepts an AG-UI body object which is passed through to the backend.
 * authToken is the Bearer token for JWT auth (AWS mode only).
 */
export async function invokeAgentCoreRuntime(
  aguiBody: Record<string, any>,
  userId: string,
  sessionId: string,
  authToken: string,
  abortSignal?: AbortSignal
): Promise<ReadableStream> {
  try {
    if (IS_LOCAL) {
      return await invokeLocalAgentCore(aguiBody, abortSignal)
    } else {
      return await invokeAwsAgentCore(aguiBody, userId, sessionId, authToken, abortSignal)
    }
  } catch (error) {
    console.error('[AgentCore] Failed to invoke Runtime:', error)
    throw new Error(
      `Failed to invoke AgentCore Runtime: ${error instanceof Error ? error.message : 'Unknown error'}`
    )
  }
}

/**
 * Extract the original session ID from an executionId.
 * executionId format: "{sessionId}:{runId}"
 */
function extractSessionId(executionId: string): string {
  const colonIdx = executionId.lastIndexOf(':')
  if (colonIdx === -1) return executionId
  return executionId.substring(0, colonIdx)
}


/**
 * Check execution status via backend ExecutionRegistry.
 * Local: GET /execution-status. Cloud: POST /invocations with action.
 * Uses the original session ID so the gateway routes to the correct container.
 */
export async function getExecutionStatus(
  executionId: string,
  userId: string,
  authToken: string,
): Promise<{ status: string }> {
  try {
    if (IS_LOCAL) {
      const response = await fetch(
        `${AGENTCORE_URL}/execution-status?executionId=${encodeURIComponent(executionId)}`,
        { method: 'GET', signal: AbortSignal.timeout(10000) }
      )
      if (!response.ok) return { status: 'not_found' }
      return await response.json()
    }

    const runtimeUrl = await getAgentCoreRuntimeUrl()
    const sessionId = extractSessionId(executionId)
    const payload = {
      threadId: sessionId,
      runId: crypto.randomUUID(),
      messages: [],
      tools: [],
      context: [],
      state: { action: 'execution_status', execution_id: executionId, user_id: userId }
    }
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': sessionId,
    }
    if (authToken) headers['Authorization'] = authToken

    const response = await fetch(runtimeUrl, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(10000),
    })
    if (!response.ok) return { status: 'not_found' }
    return await response.json()
  } catch (error) {
    console.error('[AgentCore] getExecutionStatus failed:', error)
    return { status: 'not_found' }
  }
}


/**
 * Resume an execution stream from a cursor position.
 * Local: GET /resume. Cloud: POST /invocations with action.
 * Uses the original session ID so the gateway routes to the correct container.
 */
export async function resumeExecution(
  executionId: string,
  cursor: number,
  userId: string,
  authToken: string,
  abortSignal?: AbortSignal,
): Promise<ReadableStream> {
  if (IS_LOCAL) {
    const url = `${AGENTCORE_URL}/resume?executionId=${encodeURIComponent(executionId)}&cursor=${cursor}`
    const response = await fetch(url, {
      method: 'GET',
      headers: { 'Accept': 'text/event-stream' },
      signal: abortSignal,
    })
    if (!response.ok) {
      throw new Error(`Resume failed: ${response.status}`)
    }
    if (!response.body) throw new Error('No response stream from resume')
    return response.body
  }

  const runtimeUrl = await getAgentCoreRuntimeUrl()
  const sessionId = extractSessionId(executionId)
  const payload = {
    threadId: sessionId,
    runId: crypto.randomUUID(),
    messages: [],
    tools: [],
    context: [],
    state: { action: 'resume', execution_id: executionId, cursor, user_id: userId }
  }
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Accept': 'text/event-stream',
    'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': sessionId,
  }
  if (authToken) headers['Authorization'] = authToken

  const response = await fetch(runtimeUrl, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
    signal: abortSignal,
  })
  if (!response.ok) {
    const errorText = await response.text()
    throw new Error(`Resume failed: ${response.status}: ${errorText}`)
  }
  if (!response.body) throw new Error('No response stream from resume')
  return response.body
}


export async function pingAgentCoreRuntime(sessionId?: string, userId?: string, authToken?: string): Promise<{
  success: boolean
  latencyMs: number
  mode: 'local' | 'aws'
  error?: string
}> {
  const startTime = Date.now()
  console.log(`[AgentCore Warmup] Starting (IS_LOCAL=${IS_LOCAL})`)

  try {
    if (IS_LOCAL) {
      console.log(`[AgentCore Warmup] Local mode: GET ${AGENTCORE_URL}/ping`)
      const response = await fetch(`${AGENTCORE_URL}/ping`, { method: 'GET' })
      const latencyMs = Date.now() - startTime
      if (!response.ok) throw new Error(`Ping failed: ${response.status}`)
      console.log(`[AgentCore Warmup] Local ping success: ${latencyMs}ms`)
      return { success: true, latencyMs, mode: 'local' }
    }

    const runtimeUrl = await getAgentCoreRuntimeUrl()
    console.log(`[AgentCore Warmup] Runtime URL: ${runtimeUrl}`)

    const warmupSessionId = sessionId || `warmup00_${Date.now().toString(36)}_${crypto.randomUUID().replace(/-/g, '')}`
    const warmupUserId = userId || 'anonymous'
    console.log(`[AgentCore Warmup] Invoking with sessionId=${warmupSessionId}, userId=${warmupUserId}`)

    const payload = {
      threadId: warmupSessionId,
      runId: crypto.randomUUID(),
      messages: [],
      tools: [],
      context: [],
      state: { action: 'warmup', user_id: warmupUserId }
    }

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': warmupSessionId,
    }

    if (authToken) {
      headers['Authorization'] = authToken
    }

    const response = await fetch(runtimeUrl, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(30000),
    })

    const latencyMs = Date.now() - startTime

    if (!response.ok) {
      const errorText = await response.text()
      throw new Error(`Warmup returned ${response.status}: ${errorText}`)
    }

    console.log(`[AgentCore Warmup] AWS invoke success: ${latencyMs}ms`)
    return { success: true, latencyMs, mode: 'aws' }
  } catch (error) {
    const latencyMs = Date.now() - startTime
    const errorMsg = error instanceof Error ? error.message : 'Unknown error'
    console.error(`[AgentCore Warmup] Failed after ${latencyMs}ms: ${errorMsg}`)
    if (error instanceof Error && error.stack) {
      console.error(`[AgentCore Warmup] Stack: ${error.stack}`)
    }
    return { success: false, latencyMs, mode: IS_LOCAL ? 'local' : 'aws', error: errorMsg }
  }
}

export async function validateAgentCoreConfig(): Promise<{
  configured: boolean
  url?: string
  runtimeUrl?: string
  error?: string
}> {
  try {
    if (IS_LOCAL) {
      const response = await fetch(`${AGENTCORE_URL}/health`, {
        method: 'GET',
      })

      if (!response.ok) {
        throw new Error(`Health check failed with status ${response.status}`)
      }

      return {
        configured: true,
        url: AGENTCORE_URL,
      }
    } else {
      const runtimeUrl = await getAgentCoreRuntimeUrl()
      return {
        configured: true,
        runtimeUrl,
      }
    }
  } catch (error) {
    return {
      configured: false,
      error: error instanceof Error ? error.message : 'Unknown error',
    }
  }
}
