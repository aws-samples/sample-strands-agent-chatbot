import {
  DynamoDBClient,
  GetItemCommand,
  QueryCommand,
  type AttributeValue,
} from '@aws-sdk/client-dynamodb'
import { unmarshall } from '@aws-sdk/util-dynamodb'
import type {
  SessionFilePage,
  SessionFileRecord,
  SessionFileRef,
} from './types'

const region = process.env.AWS_REGION || 'us-west-2'
const SAFE_ID = /^[A-Za-z0-9_-]+$/
const PAGE_SIZE = 200

function validateIdentity(value: string, label: string): void {
  if (!value || !SAFE_ID.test(value)) {
    throw new Error(`Invalid ${label}`)
  }
}

function sessionKey(userId: string, sessionId: string): string {
  validateIdentity(userId, 'userId')
  validateIdentity(sessionId, 'sessionId')
  return `USER#${userId}#SESSION#${sessionId}`
}

function toRef(record: SessionFileRecord): SessionFileRef {
  return {
    fileId: record.fileId,
    filename: record.filename,
    mediaType: record.mediaType,
    artifactType: record.artifactType,
    role: record.role,
    state: record.state,
    revision: record.revision,
    sizeBytes: record.sizeBytes,
    checksumSha256: record.checksumSha256,
    updatedAt: record.updatedAt,
  }
}

function encodeCursor(key: Record<string, AttributeValue>): string {
  return Buffer.from(JSON.stringify(key), 'utf8').toString('base64url')
}

function decodeCursor(cursor?: string): Record<string, AttributeValue> | undefined {
  if (!cursor) return undefined
  try {
    return JSON.parse(Buffer.from(cursor, 'base64url').toString('utf8'))
  } catch {
    throw new Error('Invalid session file cursor')
  }
}

export class DynamoSessionFileRepository {
  private readonly client: DynamoDBClient
  private readonly tableName: string

  constructor(options?: {
    client?: DynamoDBClient
    tableName?: string
  }) {
    this.client = options?.client || new DynamoDBClient({ region })
    this.tableName = options?.tableName || process.env.SESSION_FILES_TABLE || ''
    if (!this.tableName) throw new Error('SESSION_FILES_TABLE is required')
  }

  async get(
    userId: string,
    sessionId: string,
    fileId: string,
  ): Promise<SessionFileRecord | null> {
    validateIdentity(fileId, 'fileId')
    const response = await this.client.send(new GetItemCommand({
      TableName: this.tableName,
      Key: {
        sessionKey: { S: sessionKey(userId, sessionId) },
        recordKey: { S: `FILE#${fileId}` },
      },
      ConsistentRead: true,
    }))
    return response.Item
      ? unmarshall(response.Item) as SessionFileRecord
      : null
  }

  async list(
    userId: string,
    sessionId: string,
    cursor?: string,
  ): Promise<SessionFilePage> {
    const response = await this.client.send(new QueryCommand({
      TableName: this.tableName,
      KeyConditionExpression: (
        'sessionKey = :sessionKey AND begins_with(recordKey, :filePrefix)'
      ),
      ExpressionAttributeValues: {
        ':sessionKey': { S: sessionKey(userId, sessionId) },
        ':filePrefix': { S: 'FILE#' },
      },
      ExclusiveStartKey: decodeCursor(cursor),
      Limit: PAGE_SIZE,
      ScanIndexForward: false,
    }))
    const files = (response.Items || [])
      .map(item => unmarshall(item) as SessionFileRecord)
      .filter(item => item.state !== 'DELETED')
      .map(toRef)
    return {
      files,
      nextCursor: response.LastEvaluatedKey
        ? encodeCursor(response.LastEvaluatedKey)
        : undefined,
    }
  }
}
