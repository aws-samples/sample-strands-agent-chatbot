import fs from 'fs'
import path from 'path'
import {
  DynamoDBClient,
  GetItemCommand,
  QueryCommand,
  UpdateItemCommand,
  type AttributeValue,
} from '@aws-sdk/client-dynamodb'
import { unmarshall } from '@aws-sdk/util-dynamodb'

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'
const AWS_REGION =
  process.env.AWS_REGION ||
  process.env.NEXT_PUBLIC_AWS_REGION ||
  'us-west-2'
const TABLE_NAME = process.env.SESSION_ORCHESTRATION_TABLE || ''

export type DelegationExecutionStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'timed_out'

export type DelegationDeliveryStatus =
  | 'none'
  | 'pending'
  | 'published'
  | 'delivered'

export interface DelegationJob {
  jobId: string
  sessionId: string
  userId: string
  parentToolUseId?: string
  profile: 'analyst' | 'reviewer'
  executionStatus: DelegationExecutionStatus
  workStatus?: 'queued' | 'running' | 'terminal'
  deliveryStatus: DelegationDeliveryStatus
  desiredState?: 'running' | 'cancelled'
  request: {
    goal: string
    deliverable: string
    workspacePaths?: string[]
  }
  progress?: {
    content: string
    stepNumber: number
  }
  resultSummary?: string
  artifacts?: string[]
  error?: string
  createdAt: string
  updatedAt: string
  startedAt?: string
  completedAt?: string
}

function validId(value: string): boolean {
  return /^[a-zA-Z0-9_-]+$/.test(value)
}

function localJobsDir(sessionId: string): string | null {
  if (!validId(sessionId)) return null
  const sessionsDir = path.resolve(
    process.cwd(),
    '..',
    'agentcore',
    'sessions',
  )
  const jobsDir = path.resolve(
    sessionsDir,
    `session_${sessionId}`,
    'delegation_jobs',
  )
  return jobsDir.startsWith(sessionsDir + path.sep) ? jobsDir : null
}

function localJobPath(jobsDir: string, jobId: string): string | null {
  if (!/^[a-f0-9]{32}$/i.test(jobId)) return null
  const jobPath = path.resolve(jobsDir, `${jobId}.json`)
  return path.dirname(jobPath) === jobsDir ? jobPath : null
}

function readLocalJobs(
  userId: string,
  sessionId: string,
): DelegationJob[] {
  const jobsDir = localJobsDir(sessionId)
  if (!jobsDir || !fs.existsSync(jobsDir)) return []
  return fs.readdirSync(jobsDir)
    .filter(name => /^[a-f0-9]{32}\.json$/i.test(name))
    .flatMap(name => {
      try {
        const job = JSON.parse(
          fs.readFileSync(path.join(jobsDir, name), 'utf-8'),
        ) as DelegationJob & { recordType?: string }
        if (
          job.recordType !== 'DELEGATION_JOB' ||
          job.userId !== userId ||
          job.sessionId !== sessionId
        ) {
          return []
        }
        return [job]
      } catch {
        return []
      }
    })
}

export async function listDelegationJobs(
  userId: string,
  sessionId: string,
): Promise<DelegationJob[]> {
  if (IS_LOCAL) {
    return readLocalJobs(userId, sessionId)
      .sort((left, right) => left.createdAt.localeCompare(right.createdAt))
  }
  if (!TABLE_NAME) throw new Error('SESSION_ORCHESTRATION_TABLE is required')
  const client = new DynamoDBClient({ region: AWS_REGION })
  const items: Record<string, AttributeValue>[] = []
  let exclusiveStartKey: Record<string, AttributeValue> | undefined
  do {
    const response = await client.send(new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression:
        'sessionKey = :sessionKey AND begins_with(recordKey, :prefix)',
      ExpressionAttributeValues: {
        ':sessionKey': {
          S: `USER#${userId}#SESSION#${sessionId}`,
        },
        ':prefix': { S: 'JOB#' },
      },
      ConsistentRead: true,
      ExclusiveStartKey: exclusiveStartKey,
    }))
    items.push(...(response.Items || []))
    exclusiveStartKey = response.LastEvaluatedKey
  } while (exclusiveStartKey)
  return items
    .map(item => unmarshall(item) as DelegationJob & { recordType?: string })
    .filter(job => job.recordType === 'DELEGATION_JOB')
    .sort((left, right) => left.createdAt.localeCompare(right.createdAt))
}

export async function getDelegationJob(
  userId: string,
  sessionId: string,
  jobId: string,
): Promise<DelegationJob | null> {
  if (!validId(jobId)) return null
  if (IS_LOCAL) {
    return readLocalJobs(userId, sessionId)
      .find(job => job.jobId === jobId) || null
  }
  if (!TABLE_NAME) throw new Error('SESSION_ORCHESTRATION_TABLE is required')
  const response = await new DynamoDBClient({ region: AWS_REGION }).send(
    new GetItemCommand({
      TableName: TABLE_NAME,
      Key: {
        sessionKey: { S: `USER#${userId}#SESSION#${sessionId}` },
        recordKey: { S: `JOB#${jobId}` },
      },
      ConsistentRead: true,
    }),
  )
  if (!response.Item) return null
  const job = unmarshall(response.Item) as DelegationJob & {
    recordType?: string
  }
  return job.recordType === 'DELEGATION_JOB' ? job : null
}

export async function cancelDelegationJob(
  userId: string,
  sessionId: string,
  jobId: string,
): Promise<DelegationJob | null> {
  const existing = await getDelegationJob(userId, sessionId, jobId)
  if (!existing) return null
  if (
    ['succeeded', 'failed', 'cancelled', 'timed_out'].includes(
      existing.executionStatus,
    )
  ) {
    return existing
  }
  const updatedAt = new Date().toISOString()
  const ttl = Math.floor(Date.now() / 1000) + (30 * 24 * 60 * 60)
  if (IS_LOCAL) {
    const jobsDir = localJobsDir(sessionId)
    if (!jobsDir) return null
    const jobPath = localJobPath(jobsDir, jobId)
    if (!jobPath) return null
    const updated = {
      ...existing,
      desiredState: 'cancelled' as const,
      executionStatus: 'cancelled' as const,
      workStatus: 'terminal' as const,
      updatedAt,
      completedAt: updatedAt,
    }
    fs.writeFileSync(
      jobPath,
      JSON.stringify(updated, null, 2),
    )
    return updated
  }
  const client = new DynamoDBClient({ region: AWS_REGION })
  try {
    const response = await client.send(new UpdateItemCommand({
      TableName: TABLE_NAME,
      Key: {
        sessionKey: { S: `USER#${userId}#SESSION#${sessionId}` },
        recordKey: { S: `JOB#${jobId}` },
      },
      UpdateExpression:
        'SET desiredState = :cancelled, executionStatus = :cancelled, ' +
        'workStatus = :terminal, updatedAt = :updatedAt, ' +
        'completedAt = :updatedAt, #ttl = :ttl',
      ConditionExpression:
        'recordType = :recordType AND executionStatus IN (:queued, :running)',
      ExpressionAttributeNames: {
        '#ttl': 'ttl',
      },
      ExpressionAttributeValues: {
        ':cancelled': { S: 'cancelled' },
        ':terminal': { S: 'terminal' },
        ':updatedAt': { S: updatedAt },
        ':ttl': { N: ttl.toString() },
        ':recordType': { S: 'DELEGATION_JOB' },
        ':queued': { S: 'queued' },
        ':running': { S: 'running' },
      },
      ReturnValues: 'ALL_NEW',
    }))
    return response.Attributes
      ? unmarshall(response.Attributes) as DelegationJob
      : existing
  } catch (error) {
    if (
      typeof error === 'object' &&
      error !== null &&
      'name' in error &&
      (error as { name?: string }).name ===
        'ConditionalCheckFailedException'
    ) {
      return getDelegationJob(userId, sessionId, jobId)
    }
    throw error
  }
}
