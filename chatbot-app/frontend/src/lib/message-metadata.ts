import {
  DynamoDBClient,
  QueryCommand,
  UpdateItemCommand,
} from '@aws-sdk/client-dynamodb'
import { marshall, unmarshall } from '@aws-sdk/util-dynamodb'

const AWS_REGION =
  process.env.AWS_REGION || process.env.NEXT_PUBLIC_AWS_REGION || 'us-west-2'
const TABLE_NAME = process.env.SESSION_ORCHESTRATION_TABLE || ''
const ALLOWED_FIELDS = new Set([
  'feedback',
  'documents',
  'latency',
  'tokenUsage',
])

function sessionKey(userId: string, sessionId: string): string {
  return `USER#${userId}#SESSION#${sessionId}`
}

export function messageMetadataEnabled(): boolean {
  return TABLE_NAME.length > 0
}

export async function updateMessageMetadataRecord(
  userId: string,
  sessionId: string,
  logicalMessageId: string,
  metadata: Record<string, any>,
): Promise<void> {
  if (!TABLE_NAME) throw new Error('Session orchestration table is not configured')

  const entries = Object.entries(metadata).filter(([key]) => ALLOWED_FIELDS.has(key))
  if (entries.length === 0) return

  const names: Record<string, string> = {
    '#recordType': 'recordType',
    '#logicalMessageId': 'logicalMessageId',
    '#userId': 'userId',
    '#sessionId': 'sessionId',
    '#updatedAt': 'updatedAt',
  }
  const values: Record<string, any> = {
    ':recordType': 'MESSAGE_META',
    ':logicalMessageId': logicalMessageId,
    ':userId': userId,
    ':sessionId': sessionId,
    ':updatedAt': new Date().toISOString(),
  }
  const updates = [
    '#recordType = :recordType',
    '#logicalMessageId = :logicalMessageId',
    '#userId = :userId',
    '#sessionId = :sessionId',
    '#updatedAt = :updatedAt',
  ]
  entries.forEach(([key, value], index) => {
    names[`#field${index}`] = key
    values[`:field${index}`] = value
    updates.push(`#field${index} = :field${index}`)
  })

  await new DynamoDBClient({ region: AWS_REGION }).send(new UpdateItemCommand({
    TableName: TABLE_NAME,
    Key: marshall({
      sessionKey: sessionKey(userId, sessionId),
      recordKey: `MESSAGE_META#${logicalMessageId}`,
    }),
    UpdateExpression: `SET ${updates.join(', ')}`,
    ExpressionAttributeNames: names,
    ExpressionAttributeValues: marshall(values, { removeUndefinedValues: true }),
  }))
}

export async function listMessageMetadataRecords(
  userId: string,
  sessionId: string,
): Promise<Record<string, Record<string, any>>> {
  if (!TABLE_NAME) return {}

  const client = new DynamoDBClient({ region: AWS_REGION })
  const records: Record<string, Record<string, any>> = {}
  let exclusiveStartKey: Record<string, any> | undefined
  do {
    const response = await client.send(new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression:
        'sessionKey = :sessionKey AND begins_with(recordKey, :prefix)',
      ExpressionAttributeValues: {
        ':sessionKey': { S: sessionKey(userId, sessionId) },
        ':prefix': { S: 'MESSAGE_META#' },
      },
      ConsistentRead: true,
      ExclusiveStartKey: exclusiveStartKey,
    }))
    for (const item of response.Items || []) {
      const record = unmarshall(item)
      if (!record.logicalMessageId) continue
      records[record.logicalMessageId] = record
    }
    exclusiveStartKey = response.LastEvaluatedKey
  } while (exclusiveStartKey)
  return records
}
