import fs from 'fs'
import path from 'path'
import { createHash, randomUUID } from 'crypto'
import {
  BatchWriteItemCommand,
  DynamoDBClient,
  QueryCommand,
  UpdateItemCommand,
  type WriteRequest,
} from '@aws-sdk/client-dynamodb'
import { marshall, unmarshall } from '@aws-sdk/util-dynamodb'

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'
const AWS_REGION =
  process.env.AWS_REGION || process.env.NEXT_PUBLIC_AWS_REGION || 'us-west-2'
const TABLE_NAME = process.env.SESSION_ORCHESTRATION_TABLE || ''

function sessionKey(userId: string, sessionId: string): string {
  return `USER#${userId}#SESSION#${sessionId}`
}

function tombstoneLocalSession(userId: string, sessionId: string, deletedAt: string) {
  const digest = createHash('sha256')
    .update(sessionKey(userId, sessionId))
    .digest('hex')
  const mailboxDir = path.resolve(process.cwd(), '..', 'agentcore', 'sessions', 'mailbox')
  fs.mkdirSync(mailboxDir, { recursive: true })
  const mailboxPath = path.resolve(mailboxDir, `${digest}.json`)
  const data = fs.existsSync(mailboxPath)
    ? JSON.parse(fs.readFileSync(mailboxPath, 'utf-8'))
    : {
        state: { leaseEpoch: 0, version: 0 },
        events: {},
        sessionEvents: {},
      }
  data.state = {
    ...(data.state || {}),
    deletedAt,
    updatedAt: deletedAt,
    status: 'deleted',
  }
  delete data.state.leaseOwner
  delete data.state.leaseUntil
  data.events = {}
  data.sessionEvents = {}
  const temporaryPath = `${mailboxPath}.${randomUUID()}.tmp`
  fs.writeFileSync(temporaryPath, JSON.stringify(data, null, 2))
  fs.renameSync(temporaryPath, mailboxPath)
}

function mailboxPath(userId: string, sessionId: string): string {
  const digest = createHash('sha256')
    .update(sessionKey(userId, sessionId))
    .digest('hex')
  const mailboxDir = path.resolve(process.cwd(), '..', 'agentcore', 'sessions', 'mailbox')
  fs.mkdirSync(mailboxDir, { recursive: true })
  return path.resolve(mailboxDir, `${digest}.json`)
}

function writeLocalMailbox(targetPath: string, data: Record<string, any>) {
  const temporaryPath = `${targetPath}.${randomUUID()}.tmp`
  fs.writeFileSync(temporaryPath, JSON.stringify(data, null, 2))
  fs.renameSync(temporaryPath, targetPath)
}

function advanceLocalConversationEpoch(
  userId: string,
  sessionId: string,
  cutoff: string,
): number {
  const targetPath = mailboxPath(userId, sessionId)
  const data = fs.existsSync(targetPath)
    ? JSON.parse(fs.readFileSync(targetPath, 'utf-8'))
    : {
        state: { leaseEpoch: 0, version: 0, conversationEpoch: 0 },
        events: {},
        sessionEvents: {},
      }
  const updatedAt = new Date().toISOString()
  const nextEpoch = Number(data.state?.conversationEpoch || 0) + 1
  data.state = {
    ...(data.state || {}),
    conversationEpoch: nextEpoch,
    leaseEpoch: Number(data.state?.leaseEpoch || 0) + 1,
    truncatedAt: updatedAt,
    updatedAt,
  }
  delete data.state.leaseOwner
  delete data.state.leaseUntil

  const terminalTtl = Math.floor(Date.now() / 1000) + 30 * 24 * 60 * 60
  for (const event of Object.values(data.events || {}) as Record<string, any>[]) {
    if (
      Number(event.conversationEpoch || 0) < nextEpoch &&
      ['pending', 'processing'].includes(event.status)
    ) {
      Object.assign(event, {
        status: 'cancelled',
        processedAt: updatedAt,
        updatedAt,
        lastError: 'Conversation truncated',
        ttl: terminalTtl,
      })
      delete event.leaseOwner
      delete event.leaseEpoch
      delete event.eventLeaseUntil
    }
  }

  const cutoffMs = Date.parse(cutoff)
  for (const [eventId, event] of Object.entries(
    data.sessionEvents || {},
  ) as [string, Record<string, any>][]) {
    if (Date.parse(event.createdAt) >= cutoffMs) {
      delete data.sessionEvents[eventId]
    }
  }
  writeLocalMailbox(targetPath, data)

  const jobsDir = path.resolve(
    process.cwd(),
    '..',
    'agentcore',
    'sessions',
    `session_${sessionId}`,
    'delegation_jobs',
  )
  if (jobsDir.startsWith(path.resolve(
    process.cwd(),
    '..',
    'agentcore',
    'sessions',
  ) + path.sep) && fs.existsSync(jobsDir)) {
    for (const name of fs.readdirSync(jobsDir)) {
      if (!/^[a-f0-9]{32}\.json$/i.test(name)) continue
      const jobPath = path.join(jobsDir, name)
      const job = JSON.parse(fs.readFileSync(jobPath, 'utf-8'))
      if (
        job.recordType === 'DELEGATION_JOB' &&
        Number(job.conversationEpoch || 0) < nextEpoch &&
        ['queued', 'running'].includes(job.executionStatus)
      ) {
        job.desiredState = 'cancelled'
        job.updatedAt = updatedAt
        writeLocalMailbox(jobPath, job)
      }
    }
  }
  return nextEpoch
}

async function queryOrchestrationRecords(
  client: DynamoDBClient,
  userId: string,
  sessionId: string,
  prefix: string,
) {
  const records: Array<{
    key: Record<string, any>
    value: Record<string, any>
  }> = []
  let exclusiveStartKey: Record<string, any> | undefined
  do {
    const response = await client.send(new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression:
        'sessionKey = :sessionKey AND begins_with(recordKey, :prefix)',
      ExpressionAttributeValues: marshall({
        ':sessionKey': sessionKey(userId, sessionId),
        ':prefix': prefix,
      }),
      ConsistentRead: true,
      ExclusiveStartKey: exclusiveStartKey,
    }))
    for (const item of response.Items || []) {
      records.push({
        key: {
          sessionKey: item.sessionKey,
          recordKey: item.recordKey,
        },
        value: unmarshall(item),
      })
    }
    exclusiveStartKey = response.LastEvaluatedKey
  } while (exclusiveStartKey)
  return records
}

async function inBatches<T>(
  items: T[],
  size: number,
  operation: (item: T) => Promise<unknown>,
) {
  for (let index = 0; index < items.length; index += size) {
    await Promise.all(items.slice(index, index + size).map(operation))
  }
}

function isConditionalFailure(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    'name' in error &&
    (error as { name?: string }).name === 'ConditionalCheckFailedException'
  )
}

async function ignoreConditionalFailure(
  operation: () => Promise<unknown>,
): Promise<void> {
  try {
    await operation()
  } catch (error) {
    if (!isConditionalFailure(error)) throw error
  }
}

async function deleteInBatches(
  client: DynamoDBClient,
  keys: Record<string, any>[],
): Promise<void> {
  for (let index = 0; index < keys.length; index += 25) {
    let pending: WriteRequest[] = keys.slice(index, index + 25).map(key => ({
      DeleteRequest: { Key: key },
    }))
    for (let attempt = 0; pending.length > 0 && attempt < 6; attempt++) {
      const response = await client.send(new BatchWriteItemCommand({
        RequestItems: { [TABLE_NAME]: pending },
      }))
      pending = response.UnprocessedItems?.[TABLE_NAME] || []
      if (pending.length > 0) {
        await new Promise(resolve =>
          setTimeout(resolve, Math.min(1000, 50 * 2 ** attempt)),
        )
      }
    }
    if (pending.length > 0) {
      throw new Error(
        `Failed to delete ${pending.length} orchestration records`,
      )
    }
  }
}

export async function advanceSessionConversationEpoch(
  userId: string,
  sessionId: string,
  cutoff: string,
): Promise<number> {
  if (IS_LOCAL) {
    return advanceLocalConversationEpoch(userId, sessionId, cutoff)
  }
  if (!TABLE_NAME) {
    throw new Error('SESSION_ORCHESTRATION_TABLE is required for truncation')
  }

  const client = new DynamoDBClient({ region: AWS_REGION })
  const updatedAt = new Date().toISOString()
  const state = await client.send(new UpdateItemCommand({
    TableName: TABLE_NAME,
    Key: marshall({
      sessionKey: sessionKey(userId, sessionId),
      recordKey: 'STATE',
    }),
    UpdateExpression:
      'SET conversationEpoch = if_not_exists(conversationEpoch, :zero) + :one, ' +
      'leaseEpoch = if_not_exists(leaseEpoch, :zero) + :one, ' +
      'truncatedAt = :updated, updatedAt = :updated ' +
      'REMOVE leaseOwner, leaseUntil',
    ConditionExpression: 'attribute_not_exists(deletedAt)',
    ExpressionAttributeValues: marshall({
      ':zero': 0,
      ':one': 1,
      ':updated': updatedAt,
    }),
    ReturnValues: 'ALL_NEW',
  }))
  const nextEpoch = Number(
    state.Attributes ? unmarshall(state.Attributes).conversationEpoch : 0,
  )
  if (!nextEpoch) {
    throw new Error('Failed to advance conversation epoch')
  }

  const [inbox, outboxV2, legacyOutbox, jobs] = await Promise.all([
    queryOrchestrationRecords(client, userId, sessionId, 'INBOX#'),
    queryOrchestrationRecords(client, userId, sessionId, 'OUTBOX_V2#'),
    queryOrchestrationRecords(client, userId, sessionId, 'OUTBOX#'),
    queryOrchestrationRecords(client, userId, sessionId, 'JOB#'),
  ])
  const outbox = [...outboxV2, ...legacyOutbox]
  const terminalTtl = Math.floor(Date.now() / 1000) + 30 * 24 * 60 * 60

  const staleInbox = inbox.filter(({ value }) =>
    Number(value.conversationEpoch || 0) < nextEpoch &&
    ['pending', 'processing'].includes(value.status),
  )
  await inBatches(staleInbox, 10, ({ key }) =>
    ignoreConditionalFailure(() => client.send(new UpdateItemCommand({
      TableName: TABLE_NAME,
      Key: key,
      UpdateExpression:
        'SET #status = :cancelled, processedAt = :updated, updatedAt = :updated, ' +
        'lastError = :reason, #ttl = :ttl ' +
        'REMOVE leaseOwner, leaseEpoch, eventLeaseUntil',
      ConditionExpression:
        '(attribute_not_exists(conversationEpoch) OR conversationEpoch < :epoch) ' +
        'AND (#status = :pending OR #status = :processing)',
      ExpressionAttributeNames: {
        '#status': 'status',
        '#ttl': 'ttl',
      },
      ExpressionAttributeValues: marshall({
        ':cancelled': 'cancelled',
        ':updated': updatedAt,
        ':reason': 'Conversation truncated',
        ':ttl': terminalTtl,
        ':epoch': nextEpoch,
        ':pending': 'pending',
        ':processing': 'processing',
      }),
    }))),
  )

  const cutoffMs = Date.parse(cutoff)
  const staleOutbox = outbox.filter(
    ({ value }) => Date.parse(value.createdAt) >= cutoffMs,
  )
  await deleteInBatches(
    client,
    staleOutbox.map(({ key }) => key),
  )

  const staleResearchJobs = jobs.filter(({ value }) =>
    value.recordType !== 'DELEGATION_JOB' &&
    Date.parse(value.createdAt) >= cutoffMs &&
    !['cancelled', 'delivered', 'error'].includes(value.status),
  )
  await inBatches(staleResearchJobs, 10, ({ key }) =>
    ignoreConditionalFailure(() => client.send(new UpdateItemCommand({
      TableName: TABLE_NAME,
      Key: key,
      UpdateExpression:
        'SET #status = :cancelled, updatedAt = :updated, deliveryError = :reason',
      ConditionExpression:
        '#status = :queued OR #status = :running OR #status = :completed ' +
        'OR #status = :delivering',
      ExpressionAttributeNames: { '#status': 'status' },
      ExpressionAttributeValues: marshall({
        ':cancelled': 'cancelled',
        ':updated': updatedAt,
        ':reason': 'Conversation truncated',
        ':queued': 'queued',
        ':running': 'running',
        ':completed': 'completed',
        ':delivering': 'delivering',
      }),
    }))),
  )

  const staleDelegations = jobs.filter(({ value }) =>
    value.recordType === 'DELEGATION_JOB' &&
    Number(value.conversationEpoch || 0) < nextEpoch &&
    ['queued', 'running'].includes(value.executionStatus),
  )
  await inBatches(staleDelegations, 10, ({ key }) =>
    ignoreConditionalFailure(() => client.send(new UpdateItemCommand({
      TableName: TABLE_NAME,
      Key: key,
      UpdateExpression:
        'SET desiredState = :cancelled, updatedAt = :updated, ' +
        'cancellationReason = :reason',
      ConditionExpression:
        'recordType = :recordType AND ' +
        '(executionStatus = :queued OR executionStatus = :running)',
      ExpressionAttributeValues: marshall({
        ':cancelled': 'cancelled',
        ':updated': updatedAt,
        ':reason': 'Conversation truncated',
        ':recordType': 'DELEGATION_JOB',
        ':queued': 'queued',
        ':running': 'running',
      }),
    }))),
  )

  return nextEpoch
}

export async function tombstoneSessionOrchestration(
  userId: string,
  sessionId: string,
): Promise<void> {
  const deletedAt = new Date().toISOString()
  if (IS_LOCAL) {
    tombstoneLocalSession(userId, sessionId, deletedAt)
    return
  }
  if (!TABLE_NAME) return

  const client = new DynamoDBClient({ region: AWS_REGION })
  await client.send(new UpdateItemCommand({
    TableName: TABLE_NAME,
    Key: marshall({
      sessionKey: sessionKey(userId, sessionId),
      recordKey: 'STATE',
    }),
    UpdateExpression:
      'SET deletedAt = :deleted, updatedAt = :deleted, #status = :status ' +
      'REMOVE leaseOwner, leaseUntil',
    ExpressionAttributeNames: {
      '#status': 'status',
    },
    ExpressionAttributeValues: marshall({
      ':deleted': deletedAt,
      ':status': 'deleted',
    }),
  }))

  const records = (
    await Promise.all([
      queryOrchestrationRecords(client, userId, sessionId, 'INBOX#'),
      queryOrchestrationRecords(client, userId, sessionId, 'JOB#'),
      queryOrchestrationRecords(client, userId, sessionId, 'OUTBOX#'),
      queryOrchestrationRecords(client, userId, sessionId, 'OUTBOX_V2#'),
    ])
  ).flat()
  await deleteInBatches(
    client,
    records.map(({ key }) => key),
  )
}
