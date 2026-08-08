import fs from 'fs'
import path from 'path'
import { createHash, randomUUID } from 'crypto'
import {
  DeleteItemCommand,
  DynamoDBClient,
  QueryCommand,
} from '@aws-sdk/client-dynamodb'
import { unmarshall } from '@aws-sdk/util-dynamodb'

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'
const AWS_REGION =
  process.env.AWS_REGION || process.env.NEXT_PUBLIC_AWS_REGION || 'us-west-2'
const TABLE_NAME = process.env.SESSION_ORCHESTRATION_TABLE || ''

export interface SessionEventProjection {
  schemaVersion: number
  eventId: string
  eventType: string
  sessionId: string
  userId: string
  createdAt: string
  originEventId: string
  correlation: Record<string, string>
  payload: Record<string, any>
}

function sessionKey(userId: string, sessionId: string): string {
  return `USER#${userId}#SESSION#${sessionId}`
}

function readLocalEvents(
  userId: string,
  sessionId: string,
): SessionEventProjection[] {
  const digest = createHash('sha256')
    .update(sessionKey(userId, sessionId))
    .digest('hex')
  const mailboxDir = path.resolve(process.cwd(), '..', 'agentcore', 'sessions', 'mailbox')
  const mailboxPath = path.resolve(mailboxDir, `${digest}.json`)
  if (!mailboxPath.startsWith(mailboxDir + path.sep) || !fs.existsSync(mailboxPath)) {
    return []
  }

  try {
    const data = JSON.parse(fs.readFileSync(mailboxPath, 'utf-8'))
    return Object.values(data.sessionEvents || {}) as SessionEventProjection[]
  } catch {
    return []
  }
}

async function readCloudEvents(
  userId: string,
  sessionId: string,
): Promise<SessionEventProjection[]> {
  if (!TABLE_NAME) return []

  const client = new DynamoDBClient({ region: AWS_REGION })
  const events: SessionEventProjection[] = []
  let exclusiveStartKey: Record<string, any> | undefined
  do {
    const response = await client.send(new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression:
        'sessionKey = :sessionKey AND begins_with(recordKey, :prefix)',
      ExpressionAttributeValues: {
        ':sessionKey': { S: sessionKey(userId, sessionId) },
        ':prefix': { S: 'OUTBOX#' },
      },
      ConsistentRead: true,
      ExclusiveStartKey: exclusiveStartKey,
    }))
    events.push(
      ...(response.Items || []).map(
        item => unmarshall(item) as SessionEventProjection,
      ),
    )
    exclusiveStartKey = response.LastEvaluatedKey
  } while (exclusiveStartKey)

  return events
}

export async function listSessionEvents(
  userId: string,
  sessionId: string,
): Promise<SessionEventProjection[]> {
  const events = (IS_LOCAL || userId === 'anonymous')
    ? readLocalEvents(userId, sessionId)
    : await readCloudEvents(userId, sessionId)
  return events.sort(
    (left, right) =>
      left.createdAt.localeCompare(right.createdAt) ||
      left.eventId.localeCompare(right.eventId),
  )
}

export async function deleteSessionEventProjection(
  userId: string,
  sessionId: string,
  eventId: string,
): Promise<void> {
  if (IS_LOCAL || userId === 'anonymous') {
    const digest = createHash('sha256')
      .update(sessionKey(userId, sessionId))
      .digest('hex')
    const mailboxDir = path.resolve(
      process.cwd(),
      '..',
      'agentcore',
      'sessions',
      'mailbox',
    )
    const mailboxPath = path.resolve(mailboxDir, `${digest}.json`)
    if (!mailboxPath.startsWith(mailboxDir + path.sep) || !fs.existsSync(mailboxPath)) {
      return
    }
    const data = JSON.parse(fs.readFileSync(mailboxPath, 'utf-8'))
    if (!data.sessionEvents?.[eventId]) return
    delete data.sessionEvents[eventId]
    const temporaryPath = `${mailboxPath}.${randomUUID()}.tmp`
    fs.writeFileSync(temporaryPath, JSON.stringify(data, null, 2))
    fs.renameSync(temporaryPath, mailboxPath)
    return
  }

  if (!TABLE_NAME) return
  await new DynamoDBClient({ region: AWS_REGION }).send(new DeleteItemCommand({
    TableName: TABLE_NAME,
    Key: {
      sessionKey: { S: sessionKey(userId, sessionId) },
      recordKey: { S: `OUTBOX#${eventId}` },
    },
  }))
}
