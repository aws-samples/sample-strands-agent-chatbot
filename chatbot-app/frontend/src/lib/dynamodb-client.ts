/**
 * DynamoDB Client for User and Session Management
 * Single Table Design: PK=userId, SK=record type
 */

import {
  DynamoDBClient,
  GetItemCommand,
  PutItemCommand,
  UpdateItemCommand,
  QueryCommand,
} from '@aws-sdk/client-dynamodb'
import { marshall, unmarshall } from '@aws-sdk/util-dynamodb'
import type {
  UserProfile,
  UserPreferences,
  SessionMetadata,
  UserProfileRecord,
  SessionRecord,
} from './dynamodb-schema'
import {
  generateSessionSK,
  parseSessionSK,
  userRecordToProfile,
  sessionRecordToMetadata,
} from './dynamodb-schema'

// Use fallback for build time, will throw error at runtime if missing
const AWS_REGION = process.env.AWS_REGION || 'us-west-2'
const TABLE_NAME = process.env.DYNAMODB_USERS_TABLE || 'strands-agent-chatbot-users-v2'

// Initialize DynamoDB client (lazy - only used at runtime)
const dynamoClient = new DynamoDBClient({ region: AWS_REGION })

// ============================================================
// User Profile Operations
// ============================================================

/**
 * Get user profile
 */
export async function getUserProfile(userId: string): Promise<UserProfile | null> {
  try {
    const command = new GetItemCommand({
      TableName: TABLE_NAME,
      Key: marshall({
        userId,
        sk: 'PROFILE',
      }),
    })

    const response = await dynamoClient.send(command)

    if (!response.Item) {
      return null
    }

    const record = unmarshall(response.Item) as UserProfileRecord
    return userRecordToProfile(record)
  } catch (error) {
    console.error('[DynamoDB] Error getting user profile:', error)
    throw error
  }
}

/**
 * Create or update user profile
 */
export async function upsertUserProfile(
  userId: string,
  email: string,
  username?: string,
  preferences?: Partial<UserPreferences>
): Promise<UserProfile> {
  try {
    const now = new Date().toISOString()

    // Get existing profile to preserve createdAt
    const existingProfile = await getUserProfile(userId)

    const record: UserProfileRecord = {
      userId,
      sk: 'PROFILE',
      email,
      username,
      createdAt: existingProfile?.createdAt || now,
      lastAccessAt: now,
      preferences: {
        ...(existingProfile?.preferences || {}),
        ...preferences,
      },
    }

    const command = new PutItemCommand({
      TableName: TABLE_NAME,
      Item: marshall(record, { removeUndefinedValues: true }),
    })

    await dynamoClient.send(command)

    console.log(`[DynamoDB] User profile created/updated: ${userId}`)
    return userRecordToProfile(record)
  } catch (error) {
    console.error('[DynamoDB] Error upserting user profile:', error)
    throw error
  }
}

/**
 * Update user preferences
 */
export async function updateUserPreferences(
  userId: string,
  preferences: Partial<UserPreferences>
): Promise<void> {
  try {
    const profile = await getUserProfile(userId)

    if (!profile) {
      throw new Error(`User profile not found: ${userId}`)
    }

    const updatedPreferences: UserPreferences = {
      ...(profile.preferences || {}),
      ...preferences,
    }

    const now = new Date().toISOString()

    const command = new UpdateItemCommand({
      TableName: TABLE_NAME,
      Key: marshall({
        userId,
        sk: 'PROFILE',
      }),
      UpdateExpression: 'SET preferences = :prefs, lastAccessAt = :lastAccess',
      ExpressionAttributeValues: marshall({
        ':prefs': updatedPreferences,
        ':lastAccess': now,
      }, { removeUndefinedValues: true }),
    })

    await dynamoClient.send(command)

    console.log(`[DynamoDB] User preferences updated: ${userId}`)
  } catch (error) {
    console.error('[DynamoDB] Error updating user preferences:', error)
    throw error
  }
}

/**
 * Get disabled skills for a user
 */
export async function getUserDisabledSkills(userId: string): Promise<string[]> {
  try {
    const profile = await getUserProfile(userId)
    return profile?.preferences?.disabledSkills || []
  } catch (error) {
    console.error('[DynamoDB] Error getting disabled skills:', error)
    return []
  }
}

/**
 * Update disabled skills for a user
 */
export async function updateUserDisabledSkills(
  userId: string,
  disabledSkills: string[]
): Promise<void> {
  try {
    await updateUserPreferences(userId, { disabledSkills })
  } catch (error) {
    console.error('[DynamoDB] Error updating disabled skills:', error)
    throw error
  }
}

// ============================================================
// Session Operations
// ============================================================

/**
 * Create or update session metadata
 */
export async function upsertSession(
  userId: string,
  sessionId: string,
  data: {
    title?: string
    messageCount?: number
    lastMessageAt?: string
    status?: 'active' | 'archived' | 'deleted'
    starred?: boolean
    tags?: string[]
    metadata?: SessionMetadata['metadata']
  }
): Promise<SessionMetadata> {
  try {
    const now = new Date().toISOString()

    // Try to get existing session record by querying for this specific sessionId
    let existingSK: string | undefined
    let existingSession: SessionMetadata | null = null

    try {
      const command = new QueryCommand({
        TableName: TABLE_NAME,
        KeyConditionExpression: 'userId = :userId AND begins_with(sk, :sessionPrefix)',
        ExpressionAttributeValues: marshall({
          ':userId': userId,
          ':sessionPrefix': 'SESSION#',
        }),
      })
      const response = await dynamoClient.send(command)
      if (response.Items && response.Items.length > 0) {
        const records = response.Items.map((item) => unmarshall(item) as SessionRecord)
        const existingRecord = records.find((r) => r.sessionId === sessionId)
        if (existingRecord) {
          existingSK = existingRecord.sk
          existingSession = sessionRecordToMetadata(existingRecord)
          console.log(`[DynamoDB] Found existing session: ${sessionId} with SK: ${existingSK}`)
        }
      }
    } catch (error) {
      console.log('[DynamoDB] No existing session found, will create new one')
    }

    // If no existing session found, generate new SK with current timestamp
    // This ensures each new session gets a unique row
    const sessionSK = (existingSK || generateSessionSK(sessionId, now)) as `SESSION#${string}`

    // Deep merge metadata, especially metadata.messages
    const mergedMetadata: any = {
      ...(existingSession?.metadata || {}),
      ...(data.metadata || {}),
    }

    // Deep merge messages object if either exists
    if (existingSession?.metadata?.messages || data.metadata?.messages) {
      mergedMetadata.messages = {
        ...(existingSession?.metadata?.messages || {}),
        ...(data.metadata?.messages || {}),
      }
    }

    const record: SessionRecord = {
      userId,
      sk: sessionSK,
      sessionId,
      title: data.title || existingSession?.title || 'New Conversation',
      status: data.status || existingSession?.status || 'active',
      createdAt: existingSession?.createdAt || now,
      lastMessageAt: data.lastMessageAt || existingSession?.lastMessageAt || now,
      messageCount: data.messageCount ?? existingSession?.messageCount ?? 0,
      starred: data.starred ?? existingSession?.starred ?? false,
      tags: data.tags || existingSession?.tags || [],
      metadata: mergedMetadata,
    }

    const command = new PutItemCommand({
      TableName: TABLE_NAME,
      Item: marshall(record, { removeUndefinedValues: true }),
    })

    await dynamoClient.send(command)

    console.log(`[DynamoDB] Session ${existingSK ? 'updated' : 'created'}: ${sessionId} with SK: ${sessionSK}`)
    return sessionRecordToMetadata(record)
  } catch (error) {
    console.error('[DynamoDB] Error upserting session:', error)
    throw error
  }
}

/**
 * Get specific session metadata
 */
export async function getSession(
  userId: string,
  sessionId: string
): Promise<SessionMetadata | null> {
  try {
    // Query all sessions for this user and find the matching sessionId
    const command = new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: 'userId = :userId AND begins_with(sk, :sessionPrefix)',
      ExpressionAttributeValues: marshall({
        ':userId': userId,
        ':sessionPrefix': 'SESSION#',
      }),
    })

    const response = await dynamoClient.send(command)

    if (!response.Items || response.Items.length === 0) {
      return null
    }

    // Find the session with matching sessionId
    const sessionRecord = response.Items.map((item) => unmarshall(item) as SessionRecord).find(
      (record) => record.sessionId === sessionId
    )

    if (!sessionRecord) {
      return null
    }

    return sessionRecordToMetadata(sessionRecord)
  } catch (error) {
    console.error('[DynamoDB] Error getting session:', error)
    return null
  }
}

/**
 * Get user's sessions (sorted by lastMessageAt descending)
 */
export async function getUserSessions(
  userId: string,
  limit: number = 20,
  status?: 'active' | 'archived' | 'deleted'
): Promise<SessionMetadata[]> {
  try {
    const filterStatus = status || 'active'
    let sessions: SessionMetadata[] = []
    let lastEvaluatedKey: Record<string, any> | undefined

    // Paginate through results until we have enough active sessions
    do {
      const command = new QueryCommand({
        TableName: TABLE_NAME,
        KeyConditionExpression: 'userId = :userId AND begins_with(sk, :sessionPrefix)',
        ExpressionAttributeValues: marshall({
          ':userId': userId,
          ':sessionPrefix': 'SESSION#',
        }),
        ScanIndexForward: false, // Descending order (newest first)
        ...(lastEvaluatedKey ? { ExclusiveStartKey: lastEvaluatedKey } : {}),
      })

      const response = await dynamoClient.send(command)
      lastEvaluatedKey = response.LastEvaluatedKey as Record<string, any> | undefined

      if (response.Items && response.Items.length > 0) {
        const pageSessions = response.Items.map((item) => {
          const record = unmarshall(item) as SessionRecord
          return sessionRecordToMetadata(record)
        }).filter((s) => s.status === filterStatus)

        sessions.push(...pageSessions)
      }
    } while (lastEvaluatedKey && sessions.length < limit)

    // Apply limit after collecting enough filtered results
    sessions = sessions.slice(0, limit)

    // Sort by lastMessageAt descending
    sessions.sort((a, b) => new Date(b.lastMessageAt).getTime() - new Date(a.lastMessageAt).getTime())

    console.log(`[DynamoDB] Retrieved ${sessions.length} ${filterStatus} sessions for user ${userId}`)
    return sessions
  } catch (error) {
    console.error('[DynamoDB] Error getting user sessions:', error)
    return []
  }
}

/**
 * Update session metadata
 */
export async function updateSession(
  userId: string,
  sessionId: string,
  updates: {
    title?: string
    messageCount?: number
    lastMessageAt?: string
    status?: 'active' | 'archived' | 'deleted'
    starred?: boolean
    tags?: string[]
    metadata?: Partial<SessionMetadata['metadata']>
  }
): Promise<void> {
  try {
    // Get existing session to preserve SK and other fields
    const existingSession = await getSession(userId, sessionId)

    if (!existingSession) {
      throw new Error(`Session not found: ${sessionId}`)
    }

    // Deep merge metadata.messages to preserve existing message metadata
    const mergedMetadata = {
      ...(existingSession.metadata || {}),
      ...(updates.metadata || {}),
    }

    // Deep merge messages object if both exist
    if (existingSession.metadata?.messages || updates.metadata?.messages) {
      mergedMetadata.messages = {
        ...(existingSession.metadata?.messages || {}),
        ...(updates.metadata?.messages || {}),
      }
    }

    console.log(`[DynamoDB] updateSession - existing metadata: ${JSON.stringify(existingSession.metadata)}, update metadata: ${JSON.stringify(updates.metadata)}, merged: ${JSON.stringify(mergedMetadata)}`)

    // Upsert with updated values
    await upsertSession(userId, sessionId, {
      ...existingSession,
      ...updates,
      metadata: mergedMetadata,
    })

    console.log(`[DynamoDB] Session updated: ${sessionId}`)
  } catch (error) {
    console.error('[DynamoDB] Error updating session:', error)
    throw error
  }
}

/**
 * Delete session (mark as deleted)
 */
export async function deleteSession(userId: string, sessionId: string): Promise<void> {
  try {
    await updateSession(userId, sessionId, { status: 'deleted' })
    console.log(`[DynamoDB] Session deleted: ${sessionId}`)
  } catch (error) {
    console.error('[DynamoDB] Error deleting session:', error)
    throw error
  }
}

/**
 * Archive session
 */
export async function archiveSession(userId: string, sessionId: string): Promise<void> {
  try {
    await updateSession(userId, sessionId, { status: 'archived' })
    console.log(`[DynamoDB] Session archived: ${sessionId}`)
  } catch (error) {
    console.error('[DynamoDB] Error archiving session:', error)
    throw error
  }
}

/**
 * Toggle session star
 */
export async function toggleSessionStar(userId: string, sessionId: string): Promise<boolean> {
  try {
    const session = await getSession(userId, sessionId)

    if (!session) {
      throw new Error(`Session not found: ${sessionId}`)
    }

    const newStarredState = !session.starred
    await updateSession(userId, sessionId, { starred: newStarredState })

    console.log(`[DynamoDB] Session star toggled: ${sessionId} -> ${newStarredState}`)
    return newStarredState
  } catch (error) {
    console.error('[DynamoDB] Error toggling session star:', error)
    throw error
  }
}

// ============================================================
// Stop Signal Operations
// ============================================================

/**
 * Write a stop signal to DynamoDB for out-of-band stop delivery.
 * The main runtime polls this and propagates cancellation to active tools.
 */
export async function writeStopSignal(
  userId: string,
  sessionId: string,
  runId: string,
): Promise<void> {
  const ttl = Math.floor(Date.now() / 1000) + 300 // 5-minute TTL
  const command = new PutItemCommand({
    TableName: TABLE_NAME,
    Item: marshall({
      userId: `STOP#${userId}`,
      sk: `SESSION#${sessionId}`,
      runId,
      ttl,
      createdAt: new Date().toISOString(),
    }),
  })
  await dynamoClient.send(command)
  console.log(`[DynamoDB] Stop signal written for ${userId}:${sessionId}:${runId}`)
}

// ============================================================
// Tool Registry Operations (Cloud-only)
// ============================================================

export interface ToolRegistryConfig {
  local_tools: any[]
  builtin_tools: any[]
  browser_automation?: any[]
  gateway_targets?: any[]
  agentcore_runtime_a2a?: any[]
}

/**
 * Get tool registry configuration from DynamoDB (TOOL_REGISTRY user)
 * Cloud-only: Local environments should use JSON file
 * Auto-initializes from fallback config if not exists
 */
export async function getToolRegistry(fallbackConfig?: ToolRegistryConfig): Promise<ToolRegistryConfig | null> {
  try {
    const command = new GetItemCommand({
      TableName: TABLE_NAME,
      Key: marshall({
        userId: 'TOOL_REGISTRY',
        sk: 'CONFIG',
      }),
    })

    const response = await dynamoClient.send(command)

    if (!response.Item) {
      console.log('[DynamoDB] Tool registry not found in DynamoDB')

      // Auto-initialize if fallback config is provided
      if (fallbackConfig) {
        console.log('[DynamoDB] Auto-initializing TOOL_REGISTRY from fallback config...')
        await initializeToolRegistry(fallbackConfig)
        return fallbackConfig
      }

      return null
    }

    const record = unmarshall(response.Item)
    console.log('[DynamoDB] Tool registry loaded from DynamoDB')
    return record.toolRegistry as ToolRegistryConfig
  } catch (error) {
    console.error('[DynamoDB] Error getting tool registry:', error)
    return null
  }
}

/**
 * Initialize tool registry in DynamoDB with default config
 * Called automatically when TOOL_REGISTRY doesn't exist
 */
export async function initializeToolRegistry(config: ToolRegistryConfig): Promise<void> {
  try {
    const command = new PutItemCommand({
      TableName: TABLE_NAME,
      Item: marshall({
        userId: 'TOOL_REGISTRY',
        sk: 'CONFIG',
        toolRegistry: config,
        updatedAt: new Date().toISOString(),
      }, { removeUndefinedValues: true }),
    })

    await dynamoClient.send(command)
    console.log('[DynamoDB] Tool registry initialized successfully')
    console.log(`  - local_tools: ${config.local_tools?.length || 0}`)
    console.log(`  - builtin_tools: ${config.builtin_tools?.length || 0}`)
    console.log(`  - browser_automation: ${config.browser_automation?.length || 0} groups`)
    console.log(`  - gateway_targets: ${config.gateway_targets?.length || 0}`)
  } catch (error) {
    console.error('[DynamoDB] Error initializing tool registry:', error)
    throw error
  }
}
