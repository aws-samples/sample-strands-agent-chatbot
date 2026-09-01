import fs from 'fs'
import path from 'path'
import { createHash, randomUUID } from 'crypto'
import {
  BatchGetItemCommand,
  DynamoDBClient,
  GetItemCommand,
  QueryCommand,
  UpdateItemCommand,
} from '@aws-sdk/client-dynamodb'
import { marshall, unmarshall } from '@aws-sdk/util-dynamodb'
import {
  SESSION_EVENT_CURSOR_PREFIX,
  sessionEventCursor,
} from '@/lib/session-event-cursor'

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'
const AWS_REGION =
  process.env.AWS_REGION || process.env.NEXT_PUBLIC_AWS_REGION || 'us-west-2'
const TABLE_NAME = process.env.SESSION_ORCHESTRATION_TABLE || ''
const STREAM_PREFIX = SESSION_EVENT_CURSOR_PREFIX
const LEGACY_PREFIX = 'OUTBOX#'
const PAGE_SIZE = 100

export interface SessionEventProjection {
  schemaVersion: number
  eventId: string
  eventType: string
  sessionId: string
  userId: string
  createdAt: string
  originEventId: string
  conversationEpoch?: number
  correlation: Record<string, string>
  payload: Record<string, any>
}

export interface SessionEventPage {
  events: SessionEventProjection[]
  cursor: string
  conversationEpoch: number
  hasMore: boolean
}

export interface SessionAttentionState {
  latestAttentionCursor?: string
  latestAttentionAt?: string
  lastSeenAttentionCursor?: string
  lastSeenAttentionAt?: string
}

export interface ConversationFence {
  conversationEpoch: number
  cutoffByEpoch: Record<string, string>
  truncatedAt?: string
}

function sessionKey(userId: string, sessionId: string): string {
  return `USER#${userId}#SESSION#${sessionId}`
}

function conversationFenceFromState(
  state: Record<string, any> | undefined,
): ConversationFence {
  const rawCutoffs = state?.conversationEpochCutoffs
  const cutoffByEpoch: Record<string, string> = {}
  if (rawCutoffs && typeof rawCutoffs === 'object' && !Array.isArray(rawCutoffs)) {
    for (const [epoch, cutoff] of Object.entries(rawCutoffs)) {
      if (/^\d+$/.test(epoch) && typeof cutoff === 'string') {
        cutoffByEpoch[epoch] = cutoff
      }
    }
  }
  return {
    conversationEpoch: Number(state?.conversationEpoch || 0),
    cutoffByEpoch,
    ...(typeof state?.truncatedAt === 'string' && {
      truncatedAt: state.truncatedAt,
    }),
  }
}

function eventMetadataString(event: any, key: string): string | undefined {
  const value = event?.metadata?.[key]
  if (typeof value === 'string') return value
  if (typeof value?.stringValue === 'string') return value.stringValue
  return undefined
}

export function conversationEventTimestamp(
  event: any,
): string | Date | undefined {
  return event?.eventTimestamp ?? event?.eventTime
}

export function isConversationEventVisible(
  event: any,
  fence: ConversationFence,
): boolean {
  const rawEpoch = eventMetadataString(event, 'conversationEpoch')
  if (rawEpoch === undefined) return true

  const eventEpoch = Number(rawEpoch)
  if (!Number.isInteger(eventEpoch) || eventEpoch < 0) return false
  if (eventEpoch === fence.conversationEpoch) return true
  if (eventEpoch > fence.conversationEpoch) return false

  const cutoff =
    fence.cutoffByEpoch[String(eventEpoch)] ||
    (
      eventEpoch === fence.conversationEpoch - 1
        ? fence.truncatedAt
        : undefined
    )
  if (!cutoff) return false

  const timestamp = conversationEventTimestamp(event)
  const eventMs = timestamp instanceof Date
    ? timestamp.getTime()
    : new Date(timestamp || '').getTime()
  const cutoffMs = new Date(cutoff).getTime()
  return Number.isFinite(eventMs) &&
    Number.isFinite(cutoffMs) &&
    eventMs < cutoffMs
}

function localMailboxPath(userId: string, sessionId: string): string {
  const digest = createHash('sha256')
    .update(sessionKey(userId, sessionId))
    .digest('hex')
  return path.resolve(
    process.cwd(),
    '..',
    'agentcore',
    'sessions',
    'mailbox',
    `${digest}.json`,
  )
}

function readLocalPage(
  userId: string,
  sessionId: string,
  cursor?: string,
  knownEpoch?: number,
): SessionEventPage {
  const targetPath = localMailboxPath(userId, sessionId)
  if (!fs.existsSync(targetPath)) {
    return {
      events: [],
      cursor: cursor || STREAM_PREFIX,
      conversationEpoch: 0,
      hasMore: false,
    }
  }

  try {
    const data = JSON.parse(fs.readFileSync(targetPath, 'utf-8'))
    const conversationEpoch = Number(data.state?.conversationEpoch || 0)
    const epochChanged =
      knownEpoch !== undefined && knownEpoch !== conversationEpoch
    const effectiveCursor = epochChanged ? undefined : cursor
    const events = Object.values(
      data.sessionEvents || {},
    ) as SessionEventProjection[]
    const sorted = events.sort(
      (left, right) =>
        left.createdAt.localeCompare(right.createdAt) ||
        left.eventId.localeCompare(right.eventId),
    )
    const page = sorted
      .filter(event =>
        !effectiveCursor || sessionEventCursor(event) > effectiveCursor,
      )
      .slice(0, PAGE_SIZE)
    return {
      events: page,
      cursor:
        page.length > 0
          ? sessionEventCursor(page[page.length - 1])
          : effectiveCursor || STREAM_PREFIX,
      conversationEpoch,
      hasMore:
        sorted.some(event =>
          sessionEventCursor(event) >
          (page.length > 0
            ? sessionEventCursor(page[page.length - 1])
            : effectiveCursor || STREAM_PREFIX),
        ),
    }
  } catch {
    return {
      events: [],
      cursor: cursor || STREAM_PREFIX,
      conversationEpoch: 0,
      hasMore: false,
    }
  }
}

async function readConversationEpoch(
  client: DynamoDBClient,
  userId: string,
  sessionId: string,
): Promise<number> {
  const response = await client.send(new GetItemCommand({
    TableName: TABLE_NAME,
    Key: {
      sessionKey: { S: sessionKey(userId, sessionId) },
      recordKey: { S: 'STATE' },
    },
    ConsistentRead: true,
    ProjectionExpression: 'conversationEpoch',
  }))
  if (!response.Item) return 0
  return Number(unmarshall(response.Item).conversationEpoch || 0)
}

export async function getSessionConversationEpoch(
  userId: string,
  sessionId: string,
): Promise<number> {
  return (await getSessionConversationFence(userId, sessionId)).conversationEpoch
}

export async function getSessionConversationFence(
  userId: string,
  sessionId: string,
): Promise<ConversationFence> {
  if (IS_LOCAL || userId === 'anonymous') {
    const targetPath = localMailboxPath(userId, sessionId)
    if (!fs.existsSync(targetPath)) {
      return { conversationEpoch: 0, cutoffByEpoch: {} }
    }
    const data = JSON.parse(fs.readFileSync(targetPath, 'utf-8'))
    return conversationFenceFromState(data.state)
  }
  if (!TABLE_NAME) return { conversationEpoch: 0, cutoffByEpoch: {} }
  const response = await new DynamoDBClient({ region: AWS_REGION }).send(
    new GetItemCommand({
      TableName: TABLE_NAME,
      Key: {
        sessionKey: { S: sessionKey(userId, sessionId) },
        recordKey: { S: 'STATE' },
      },
      ConsistentRead: true,
      ProjectionExpression:
        'conversationEpoch, conversationEpochCutoffs, truncatedAt',
    }),
  )
  return response.Item
    ? conversationFenceFromState(unmarshall(response.Item))
    : { conversationEpoch: 0, cutoffByEpoch: {} }
}

async function queryPrefix(
  client: DynamoDBClient,
  userId: string,
  sessionId: string,
  prefix: string,
  cursor?: string,
  limit?: number,
) {
  const response = await client.send(new QueryCommand({
    TableName: TABLE_NAME,
    KeyConditionExpression:
      'sessionKey = :sessionKey AND begins_with(recordKey, :prefix)',
    ExpressionAttributeValues: {
      ':sessionKey': { S: sessionKey(userId, sessionId) },
      ':prefix': { S: prefix },
    },
    ConsistentRead: true,
    ExclusiveStartKey:
      cursor && cursor !== STREAM_PREFIX
        ? {
            sessionKey: { S: sessionKey(userId, sessionId) },
            recordKey: { S: cursor },
          }
        : undefined,
    Limit: limit,
  }))
  return {
    events: (response.Items || []).map(
      item => unmarshall(item) as SessionEventProjection,
    ),
    lastKey: response.LastEvaluatedKey?.recordKey?.S,
  }
}

async function readCloudPage(
  userId: string,
  sessionId: string,
  cursor?: string,
  knownEpoch?: number,
): Promise<SessionEventPage> {
  if (!TABLE_NAME) {
    return {
      events: [],
      cursor: cursor || STREAM_PREFIX,
      conversationEpoch: 0,
      hasMore: false,
    }
  }

  const client = new DynamoDBClient({ region: AWS_REGION })
  const conversationEpoch = await readConversationEpoch(
    client,
    userId,
    sessionId,
  )
  const epochChanged =
    knownEpoch !== undefined && knownEpoch !== conversationEpoch
  const effectiveCursor = epochChanged ? undefined : cursor
  const streamPage = await queryPrefix(
    client,
    userId,
    sessionId,
    STREAM_PREFIX,
    effectiveCursor,
    PAGE_SIZE,
  )

  let events = streamPage.events
  if (!effectiveCursor) {
    const legacy: SessionEventProjection[] = []
    let legacyCursor: string | undefined
    do {
      const page = await queryPrefix(
        client,
        userId,
        sessionId,
        LEGACY_PREFIX,
        legacyCursor,
      )
      legacy.push(...page.events)
      legacyCursor = page.lastKey
    } while (legacyCursor)
    events = [...legacy, ...events]
  }

  events.sort(
    (left, right) =>
      left.createdAt.localeCompare(right.createdAt) ||
      left.eventId.localeCompare(right.eventId),
  )
  const lastStreamEvent = streamPage.events[streamPage.events.length - 1]
  return {
    events,
    cursor:
      lastStreamEvent
        ? sessionEventCursor(lastStreamEvent)
        : effectiveCursor || STREAM_PREFIX,
    conversationEpoch,
    hasMore: Boolean(streamPage.lastKey),
  }
}

export async function listSessionEvents(
  userId: string,
  sessionId: string,
  options: {
    cursor?: string
    knownEpoch?: number
  } = {},
): Promise<SessionEventPage> {
  return (IS_LOCAL || userId === 'anonymous')
    ? readLocalPage(
        userId,
        sessionId,
        options.cursor,
        options.knownEpoch,
      )
    : readCloudPage(
        userId,
        sessionId,
        options.cursor,
        options.knownEpoch,
      )
}

function attentionStateFromRecord(
  record: Record<string, any> | undefined,
): SessionAttentionState {
  if (!record) return {}
  return {
    latestAttentionCursor:
      typeof record.latestAttentionCursor === 'string'
        ? record.latestAttentionCursor
        : undefined,
    latestAttentionAt:
      typeof record.latestAttentionAt === 'string'
        ? record.latestAttentionAt
        : undefined,
    lastSeenAttentionCursor:
      typeof record.lastSeenAttentionCursor === 'string'
        ? record.lastSeenAttentionCursor
        : undefined,
    lastSeenAttentionAt:
      typeof record.lastSeenAttentionAt === 'string'
        ? record.lastSeenAttentionAt
        : undefined,
  }
}

export function hasUnseenSessionAttention(
  state: SessionAttentionState,
): boolean {
  return Boolean(
    state.latestAttentionCursor &&
    state.latestAttentionCursor >
      (state.lastSeenAttentionCursor || STREAM_PREFIX),
  )
}

export async function getSessionAttentionStates(
  userId: string,
  sessionIds: string[],
): Promise<Map<string, SessionAttentionState>> {
  const result = new Map<string, SessionAttentionState>()
  if (sessionIds.length === 0) return result

  if (IS_LOCAL || userId === 'anonymous') {
    for (const sessionId of sessionIds) {
      const targetPath = localMailboxPath(userId, sessionId)
      if (!fs.existsSync(targetPath)) {
        result.set(sessionId, {})
        continue
      }
      try {
        const data = JSON.parse(fs.readFileSync(targetPath, 'utf-8'))
        result.set(sessionId, attentionStateFromRecord(data.state))
      } catch {
        result.set(sessionId, {})
      }
    }
    return result
  }

  if (!TABLE_NAME) {
    sessionIds.forEach(sessionId => result.set(sessionId, {}))
    return result
  }

  const client = new DynamoDBClient({ region: AWS_REGION })
  let pendingKeys = sessionIds.map(sessionId => marshall({
    sessionKey: sessionKey(userId, sessionId),
    recordKey: 'STATE',
  }))
  let attempts = 0
  while (pendingKeys.length > 0 && attempts < 3) {
    attempts += 1
    const response = await client.send(new BatchGetItemCommand({
      RequestItems: {
        [TABLE_NAME]: {
          Keys: pendingKeys,
          ConsistentRead: true,
          ProjectionExpression:
            'sessionKey, latestAttentionCursor, latestAttentionAt, ' +
            'lastSeenAttentionCursor, lastSeenAttentionAt',
        },
      },
    }))
    for (const raw of response.Responses?.[TABLE_NAME] || []) {
      const record = unmarshall(raw)
      const prefix = `USER#${userId}#SESSION#`
      const id = String(record.sessionKey || '').startsWith(prefix)
        ? String(record.sessionKey).slice(prefix.length)
        : ''
      if (id) result.set(id, attentionStateFromRecord(record))
    }
    pendingKeys = response.UnprocessedKeys?.[TABLE_NAME]?.Keys || []
  }
  sessionIds.forEach(sessionId => {
    if (!result.has(sessionId)) result.set(sessionId, {})
  })
  return result
}

export async function markSessionAttentionSeen(
  userId: string,
  sessionId: string,
  seenThroughCursor: string,
): Promise<void> {
  if (!seenThroughCursor.startsWith(STREAM_PREFIX)) {
    throw new Error('Invalid session attention cursor')
  }
  const seenAt = new Date().toISOString()

  if (IS_LOCAL || userId === 'anonymous') {
    const targetPath = localMailboxPath(userId, sessionId)
    if (!fs.existsSync(targetPath)) return
    const data = JSON.parse(fs.readFileSync(targetPath, 'utf-8'))
    const state = data.state || {}
    const latest = state.latestAttentionCursor
    const previous = state.lastSeenAttentionCursor || STREAM_PREFIX
    if (
      typeof latest !== 'string' ||
      latest < seenThroughCursor ||
      previous >= seenThroughCursor ||
      state.deletedAt
    ) {
      return
    }
    state.lastSeenAttentionCursor = seenThroughCursor
    state.lastSeenAttentionAt = seenAt
    data.state = state
    const temporaryPath = `${targetPath}.${randomUUID()}.tmp`
    fs.writeFileSync(temporaryPath, JSON.stringify(data, null, 2))
    fs.renameSync(temporaryPath, targetPath)
    return
  }

  if (!TABLE_NAME) return
  const client = new DynamoDBClient({ region: AWS_REGION })
  try {
    await client.send(new UpdateItemCommand({
      TableName: TABLE_NAME,
      Key: marshall({
        sessionKey: sessionKey(userId, sessionId),
        recordKey: 'STATE',
      }),
      UpdateExpression:
        'SET lastSeenAttentionCursor = :cursor, lastSeenAttentionAt = :seenAt',
      ConditionExpression:
        'attribute_not_exists(deletedAt) AND latestAttentionCursor >= :cursor ' +
        'AND (attribute_not_exists(lastSeenAttentionCursor) ' +
        'OR lastSeenAttentionCursor < :cursor)',
      ExpressionAttributeValues: marshall({
        ':cursor': seenThroughCursor,
        ':seenAt': seenAt,
      }),
    }))
  } catch (error: any) {
    if (error?.name !== 'ConditionalCheckFailedException') throw error
  }
}
