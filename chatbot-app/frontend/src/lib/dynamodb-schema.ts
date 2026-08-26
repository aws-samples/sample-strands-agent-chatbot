/**
 * DynamoDB Single Table Schema for User and Session Management
 *
 * Single Table Design:
 * - PK: userId (Cognito sub UUID)
 * - SK: Record type discriminator
 *   - "PROFILE" for user profile
 *   - "SESSION#{timestamp}#{sessionId}" for session metadata
 *
 * Benefits:
 * - Single table for all user data
 * - Efficient queries (get profile + sessions in one query)
 * - No GSI needed
 * - Cost effective
 */

// ============================================================
// DynamoDB Record Types
// ============================================================

/**
 * Base DynamoDB record interface
 */
export interface DynamoDBRecord {
  userId: string  // PK - Cognito sub (UUID)
  sk: string      // SK - Record type discriminator
  ttl?: number    // Unix timestamp for auto-deletion (optional)
}

/**
 * User Profile Record
 * PK: userId
 * SK: "PROFILE"
 */
export interface UserProfileRecord extends DynamoDBRecord {
  sk: 'PROFILE'
  email: string
  username?: string
  createdAt: string // ISO 8601 timestamp
  lastAccessAt: string // ISO 8601 timestamp
  preferences: UserPreferences
  metadata?: Record<string, any>
}

/**
 * Session Metadata Record
 * PK: userId
 * SK: "SESSION#{timestamp}#{sessionId}"
 *
 * Example SK: "SESSION#2025-01-15T10:30:00Z#550e8400_1kj3h2_x9s8k2"
 * This allows sorting sessions by creation time
 */
export interface SessionRecord extends DynamoDBRecord {
  sk: `SESSION#${string}` // Pattern: SESSION#{timestamp}#{sessionId}
  sessionId: string
  title: string
  status: 'active' | 'archived' | 'deleted'
  createdAt: string // ISO 8601
  lastMessageAt: string // ISO 8601
  messageCount: number
  tags?: string[]
  starred?: boolean
  metadata?: {
    lastModel?: string
    lastTemperature?: number
    totalTokens?: number
    agentCoreTraceId?: string
    [key: string]: any
  }
}

// ============================================================
// User Preferences
// ============================================================

/**
 * User API Keys for external services
 * Stored encrypted in DynamoDB
 */
export interface UserApiKeys {
  tavily_api_key?: string
  google_api_key?: string
  google_search_engine_id?: string
  google_maps_api_key?: string
}

export interface UserPreferences {
  // Model Configuration
  defaultModel?: string // e.g., "us.openai.gpt-5.6-terra"
  defaultTemperature?: number // 0.0 - 1.0
  systemPrompt?: string // Custom system prompt
  customPromptName?: string // Name of custom prompt
  cachingEnabled?: boolean // Prompt caching enabled

  // Tool Configuration
  enabledTools?: string[] // Array of tool IDs
  disabledTools?: string[] // Array of tool IDs to explicitly disable
  disabledSkills?: string[] // Skill names the user has opted out of

  // API Keys for external services
  apiKeys?: UserApiKeys

  // UI Preferences
  theme?: 'light' | 'dark' | 'auto'
  language?: string // e.g., "en", "ko", "ja"

  // Notification Settings
  emailNotifications?: boolean

  // Custom Settings
  [key: string]: any // Allow additional custom preferences
}

// ============================================================
// Helper Types (Application Layer)
// ============================================================

/**
 * User Profile (application-friendly format)
 * Excludes DynamoDB-specific fields
 */
export interface UserProfile {
  userId: string
  email: string
  username?: string
  createdAt: string
  lastAccessAt: string
  preferences: UserPreferences
  metadata?: Record<string, any>
}

/**
 * Session Metadata (application-friendly format)
 */
export interface SessionMetadata {
  sessionId: string
  userId: string
  title: string
  status: 'active' | 'archived' | 'deleted'
  createdAt: string
  lastMessageAt: string
  messageCount: number
  tags?: string[]
  starred?: boolean
  metadata?: {
    lastModel?: string
    lastTemperature?: number
    totalTokens?: number
    agentCoreTraceId?: string
    browserSession?: {
      sessionId: string | null
      browserId: string | null
    }
    [key: string]: any
  }
}

// ============================================================
// Input Types for CRUD Operations
// ============================================================

export interface CreateUserInput {
  userId: string
  email: string
  username?: string
  preferences?: Partial<UserPreferences>
}

export interface UpdateUserPreferencesInput {
  userId: string
  preferences: Partial<UserPreferences>
}

export interface CreateSessionInput {
  sessionId: string
  userId: string
  title: string
  metadata?: SessionMetadata['metadata']
}

export interface UpdateSessionInput {
  sessionId: string
  userId: string
  title?: string
  status?: SessionMetadata['status']
  tags?: string[]
  starred?: boolean
  metadata?: Partial<SessionMetadata['metadata']>
}

// ============================================================
// Query Patterns
// ============================================================

/**
 * Query Examples:
 *
 * 1. Get user profile:
 *    GetItem(PK=userId, SK="PROFILE")
 *
 * 2. Get all user sessions (sorted by creation time, descending):
 *    Query(PK=userId, SK begins_with "SESSION#", ScanIndexForward=false, Limit=20)
 *
 * 3. Get specific session:
 *    Query(PK=userId, SK="SESSION#{timestamp}#{sessionId}")
 *    Or use sessionId index if needed
 *
 * 4. Get profile + recent sessions in one query:
 *    Query(PK=userId, Limit=21)  // First item is PROFILE, rest are SESSIONs
 *
 * 5. Delete user and all sessions:
 *    Query(PK=userId) to get all items, then BatchWrite delete
 */

// ============================================================
// Utility Functions
// ============================================================

/**
 * Generate SK for session record
 */
export function generateSessionSK(sessionId: string, timestamp?: string): string {
  const ts = timestamp || new Date().toISOString()
  return `SESSION#${ts}#${sessionId}`
}

/**
 * Parse sessionId from SK
 */
export function parseSessionSK(sk: string): { timestamp: string; sessionId: string } | null {
  const match = sk.match(/^SESSION#(.+?)#(.+)$/)
  if (!match) return null
  return {
    timestamp: match[1],
    sessionId: match[2],
  }
}

/**
 * Convert DynamoDB record to application format
 */
export function userRecordToProfile(record: UserProfileRecord): UserProfile {
  const { sk, ...profile } = record
  return profile
}

export function sessionRecordToMetadata(record: SessionRecord): SessionMetadata {
  const { sk, ttl, ...metadata } = record
  return metadata
}

// ============================================================
// Example Data
// ============================================================

export const EXAMPLE_USER_RECORD: UserProfileRecord = {
  userId: '550e8400-e29b-41d4-a716-446655440000',
  sk: 'PROFILE',
  email: 'user@example.com',
  username: 'john_doe',
  createdAt: '2025-01-14T10:00:00Z',
  lastAccessAt: '2025-01-14T15:30:00Z',
  preferences: {
    defaultModel: 'us.openai.gpt-5.6-terra',
    defaultTemperature: 0.5,
    systemPrompt: 'You are a helpful AI assistant.',
    enabledTools: ['calculator', 'web_search', 'code_interpreter'],
    theme: 'dark',
    language: 'en',
  },
}

export const EXAMPLE_SESSION_RECORD: SessionRecord = {
  userId: '550e8400-e29b-41d4-a716-446655440000',
  sk: 'SESSION#2025-01-14T14:00:00Z#550e8400_1kj3h2_x9s8k2',
  sessionId: '550e8400_1kj3h2_x9s8k2',
  title: 'Help with AWS Lambda deployment',
  status: 'active',
  createdAt: '2025-01-14T14:00:00Z',
  lastMessageAt: '2025-01-14T15:30:00Z',
  messageCount: 12,
  tags: ['aws', 'lambda', 'deployment'],
  starred: true,
  metadata: {
    lastModel: 'us.openai.gpt-5.6-terra',
    lastTemperature: 0.5,
    totalTokens: 8500,
    agentCoreTraceId: 'trace-abc123',
  },
}
