/**
 * Elicitation Complete API endpoint
 *
 * The OAuth callback page posts here after AgentCore Identity redirects the
 * user's browser back with session_id + state (= elicitation ID). This is the
 * "user verification" step of AgentCore's session-binding flow: we verify the
 * caller's Cognito session and that they own the pending elicitation, then
 * write the completion signal to the shared DynamoDB store that the
 * orchestrator's elicitation_bridge polls. The bridge then calls
 * CompleteResourceTokenAuth from its own workload identity context.
 *
 * We do NOT call the orchestrator runtime here — the runtime only trusts user
 * JWTs, and the BFF (ECS task) already has IAM permissions on the shared
 * DynamoDB table.
 */
import { NextRequest, NextResponse } from 'next/server'
import {
  DynamoDBClient,
  GetItemCommand,
  UpdateItemCommand,
} from '@aws-sdk/client-dynamodb'
import { extractUserFromRequest } from '@/lib/auth-utils'

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
    const elicitationId: string | undefined = body.elicitationId
    const oauthSessionUri: string | undefined = body.oauthSessionUri

    if (!elicitationId || !oauthSessionUri) {
      return NextResponse.json(
        { error: 'elicitationId and oauthSessionUri are required' },
        { status: 400 }
      )
    }

    const key = {
      userId: { S: `ELICIT#${elicitationId}` },
      sk: { S: 'META' },
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

    // warn, not log: removeConsole strips console.log from production, which
    // would leave the 3LO completion path with no server-side trace at all.
    console.warn(`[Elicitation] Signalled in DynamoDB: user=${user.userId}, eid=${elicitationId}`)

    return NextResponse.json({ success: true })

  } catch (error) {
    console.error('[Elicitation] Error:', error)
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}
