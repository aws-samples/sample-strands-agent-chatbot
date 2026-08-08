import fs from 'fs'
import path from 'path'
import { createHash, randomUUID } from 'crypto'
import { DynamoDBClient, UpdateItemCommand } from '@aws-sdk/client-dynamodb'
import { marshall } from '@aws-sdk/util-dynamodb'

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
  const temporaryPath = `${mailboxPath}.${randomUUID()}.tmp`
  fs.writeFileSync(temporaryPath, JSON.stringify(data, null, 2))
  fs.renameSync(temporaryPath, mailboxPath)
}

export async function tombstoneSessionOrchestration(
  userId: string,
  sessionId: string,
): Promise<void> {
  const deletedAt = new Date().toISOString()
  if (IS_LOCAL || userId === 'anonymous') {
    tombstoneLocalSession(userId, sessionId, deletedAt)
    return
  }
  if (!TABLE_NAME) return

  await new DynamoDBClient({ region: AWS_REGION }).send(new UpdateItemCommand({
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
}
