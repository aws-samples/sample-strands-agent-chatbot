/**
 * Authentication utilities for extracting user info from Cognito JWT tokens
 */
import { CognitoJwtVerifier } from 'aws-jwt-verify'

interface CognitoUser {
  userId: string
  email?: string
  username?: string
  tokenExpiresAt?: number
}

let verifier: ReturnType<typeof CognitoJwtVerifier.create> | null = null
let verifierConfig = ''

function getVerifier() {
  const userPoolId = process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID
  const clientId = process.env.NEXT_PUBLIC_COGNITO_USER_POOL_CLIENT_ID
  if (!userPoolId || !clientId) return null

  const config = `${userPoolId}:${clientId}`
  if (!verifier || verifierConfig !== config) {
    verifier = CognitoJwtVerifier.create({
      userPoolId,
      tokenUse: 'access',
      clientId,
    })
    verifierConfig = config
  }
  return verifier
}

/**
 * Extract user information from Cognito JWT token in Authorization header
 */
export async function extractUserFromRequest(request: Request): Promise<CognitoUser> {
  try {
    const authHeader = request.headers.get('authorization')
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      return { userId: 'anonymous' }
    }

    const cognitoVerifier = getVerifier()
    if (!cognitoVerifier) {
      console.warn('[Auth] Cognito verifier is not configured')
      return { userId: 'anonymous' }
    }

    const payload = await cognitoVerifier.verify(authHeader.substring(7))

    // Cognito access tokens: sub, username, client_id
    const userIdClaim = payload.sub || payload['cognito:username'] || payload.username
    const userId = typeof userIdClaim === 'string' ? userIdClaim : 'anonymous'
    const email = typeof payload.email === 'string' ? payload.email : undefined
    const usernameClaim = payload['cognito:username'] || payload.username
    const username = typeof usernameClaim === 'string' ? usernameClaim : undefined
    const tokenExpiresAt = typeof payload.exp === 'number' ? payload.exp : undefined

    console.log(`[Auth] Authenticated user: ${userId} (${email || username || 'no email'})`)

    return {
      userId,
      email,
      username,
      tokenExpiresAt,
    }
  } catch (error) {
    console.warn('[Auth] JWT verification failed:', error instanceof Error ? error.message : error)
    return { userId: 'anonymous' }
  }
}

/**
 * Generate or extract session ID from request headers
 * Session ID must be >= 33 characters to meet AgentCore Runtime validation
 */
export function getSessionId(request: Request, userId: string): { sessionId: string } {
  // Check for existing session ID in header
  const headerSessionId = request.headers.get('X-Session-ID')
  if (headerSessionId) {
    return { sessionId: headerSessionId }
  }

  // Generate new session ID >= 33 characters
  // Format: userPrefix_timestamp_randomUUID (approx 50+ chars)
  const timestamp = Date.now().toString(36)  // ~10 chars
  const randomId = crypto.randomUUID().replace(/-/g, '')  // 32 hex chars
  const userPrefix = userId !== 'anonymous' ? userId.substring(0, 8) : 'anon0000'  // 8 chars

  const sessionId = `${userPrefix}_${timestamp}_${randomId}`

  console.log(`[Auth] Generated session ID (length: ${sessionId.length})`)

  return { sessionId }
}

// Check if running in local development mode
const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'

interface SessionData {
  title: string
  messageCount?: number
  lastMessageAt?: string
  status?: 'active' | 'archived' | 'deleted'
  starred?: boolean
  tags?: string[]
  metadata?: Record<string, any>
}

/**
 * Ensure session exists in storage (DynamoDB or local file)
 * Creates session if it doesn't exist, returns isNew flag
 */
export async function ensureSessionExists(
  userId: string,
  sessionId: string,
  defaultData: SessionData
): Promise<{ isNew: boolean }> {
  const now = new Date().toISOString()
  const sessionData = {
    ...defaultData,
    messageCount: defaultData.messageCount ?? 0,
    lastMessageAt: defaultData.lastMessageAt ?? now,
    status: defaultData.status ?? 'active' as const,
    starred: defaultData.starred ?? false,
    tags: defaultData.tags ?? [],
  }

  if (IS_LOCAL) {
    const { getSession, upsertSession } = await import('@/lib/local-session-store')
    const existingSession = getSession(userId, sessionId)
    if (!existingSession) {
      upsertSession(userId, sessionId, sessionData)
      console.log(`[Session] Created new local session: ${sessionId}`)
      return { isNew: true }
    }
    return { isNew: false }
  } else {
    const { getSession, upsertSession } = await import('@/lib/dynamodb-client')
    const existingSession = await getSession(userId, sessionId)
    if (!existingSession) {
      await upsertSession(userId, sessionId, sessionData)
      console.log(`[Session] Created new DynamoDB session: ${sessionId}`)
      return { isNew: true }
    }
    return { isNew: false }
  }
}
