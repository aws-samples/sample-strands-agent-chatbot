/**
 * Elicitation Complete API endpoint
 *
 * Writes the OAuth completion signal directly to the shared DynamoDB store
 * that the orchestrator's elicitation_bridge reads. We do NOT call the
 * orchestrator runtime here — the runtime only trusts user JWTs, and this
 * request originates from a popup context where the Amplify session is
 * sometimes not hydrated yet. The BFF (ECS task) has IAM permissions on the
 * shared DynamoDB users table, so it can write the signal directly.
 *
 * Keeping the backend's /invocations handler for `elicitation_complete`
 * is also fine for local/dev testing, but cloud traffic skips it.
 */
import { NextRequest, NextResponse } from 'next/server'
import {
  DynamoDBClient,
  GetItemCommand,
  UpdateItemCommand,
} from '@aws-sdk/client-dynamodb'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { getSession } from '@/lib/dynamodb-client'

const AWS_REGION = process.env.AWS_REGION || 'us-west-2'
const TABLE_NAME = process.env.DYNAMODB_USERS_TABLE || 'strands-agent-chatbot-users-v2'

const dynamoClient = new DynamoDBClient({ region: AWS_REGION })

const COMPLETION_TTL_SECONDS = 600

export async function POST(request: NextRequest) {
  try {
    const user = await extractUserFromRequest(request)
    if (user.userId === 'anonymous') {
      return NextResponse.json({ error: 'Authentication required' }, { status: 401 })
    }

    const body = await request.json().catch(() => ({}))
    const sessionId: string | undefined = body.sessionId
    const elicitationId: string | undefined = body.elicitationId
    const oauthSessionUri: string | undefined = body.oauthSessionUri

    if (!sessionId || !elicitationId || !oauthSessionUri) {
      return NextResponse.json(
        { error: 'sessionId, elicitationId, and oauthSessionUri are required' },
        { status: 400 }
      )
    }

    const session = await getSession(user.userId, sessionId)
    if (!session) {
      return NextResponse.json({ error: 'Session not found' }, { status: 404 })
    }

    const key = {
      userId: { S: `ELICIT#${sessionId}` },
      sk: { S: `EID#${elicitationId}` },
    }
    const pending = await dynamoClient.send(new GetItemCommand({
      TableName: TABLE_NAME,
      Key: key,
      ConsistentRead: true,
    }))
    const pendingItem = pending.Item
    if (
      !pendingItem ||
      pendingItem.status?.S !== 'pending' ||
      pendingItem.ownerUserId?.S !== user.userId
    ) {
      return NextResponse.json({ error: 'OAuth request not found' }, { status: 404 })
    }

    const now = Math.floor(Date.now() / 1000)
    await dynamoClient.send(new UpdateItemCommand({
      TableName: TABLE_NAME,
      Key: key,
      UpdateExpression: 'SET #status = :completed, oauthSessionUri = :uri, #ttl = :ttl',
      ConditionExpression: '#status = :pending AND ownerUserId = :owner',
      ExpressionAttributeNames: {
        '#status': 'status',
        '#ttl': 'ttl',
      },
      ExpressionAttributeValues: {
        ':pending': { S: 'pending' },
        ':completed': { S: 'completed' },
        ':owner': { S: user.userId },
        ':uri': { S: oauthSessionUri },
        ':ttl': { N: String(now + COMPLETION_TTL_SECONDS) },
      },
    }))

    console.log(`[Elicitation] Signalled in DynamoDB: user=${user.userId}, session=${sessionId}, eid=${elicitationId}`)

    return NextResponse.json({ success: true })

  } catch (error) {
    console.error('[Elicitation] Error:', error)
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}
