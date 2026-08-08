import fs from 'fs'
import path from 'path'
import { createHash } from 'crypto'
import {
  DynamoDBClient,
  GetItemCommand,
  QueryCommand,
} from '@aws-sdk/client-dynamodb'
import { unmarshall } from '@aws-sdk/util-dynamodb'

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'
const AWS_REGION =
  process.env.AWS_REGION || process.env.NEXT_PUBLIC_AWS_REGION || 'us-west-2'
const TABLE_NAME = process.env.SESSION_ORCHESTRATION_TABLE || ''
const STREAM_PREFIX = 'OUTBOX_V2#'
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

function sessionKey(userId: string, sessionId: string): string {
  return `USER#${userId}#SESSION#${sessionId}`
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

function projectionCursor(event: SessionEventProjection): string {
  return `${STREAM_PREFIX}${event.createdAt}#${event.eventId}`
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
        !effectiveCursor || projectionCursor(event) > effectiveCursor,
      )
      .slice(0, PAGE_SIZE)
    return {
      events: page,
      cursor:
        page.length > 0
          ? projectionCursor(page[page.length - 1])
          : effectiveCursor || STREAM_PREFIX,
      conversationEpoch,
      hasMore:
        sorted.some(event =>
          projectionCursor(event) >
          (page.length > 0
            ? projectionCursor(page[page.length - 1])
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
  if (IS_LOCAL || userId === 'anonymous') {
    const targetPath = localMailboxPath(userId, sessionId)
    if (!fs.existsSync(targetPath)) return 0
    const data = JSON.parse(fs.readFileSync(targetPath, 'utf-8'))
    return Number(data.state?.conversationEpoch || 0)
  }
  if (!TABLE_NAME) return 0
  return readConversationEpoch(
    new DynamoDBClient({ region: AWS_REGION }),
    userId,
    sessionId,
  )
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
        ? projectionCursor(lastStreamEvent)
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
