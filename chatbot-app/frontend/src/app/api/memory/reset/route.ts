/**
 * Memory Reset API - Reset/Delete user's Long-Term Memory records
 *
 * DELETE /api/memory/reset
 *   - Deletes all LTM records for the authenticated user
 *   - Supports selective deletion by namespace (preferences, facts, summaries)
 *
 * GET /api/memory/reset
 *   - Get memory stats for the user (record counts by type)
 *
 * Query Parameters:
 *   - namespace: Optional. One of 'preferences', 'facts', 'summaries', or 'all' (default: 'all')
 */
import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import {
  BedrockAgentCoreClient,
  ListMemoryRecordsCommand,
  DeleteMemoryRecordCommand,
} from '@aws-sdk/client-bedrock-agentcore'
import { SSMClient, GetParameterCommand } from '@aws-sdk/client-ssm'

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'
const AWS_REGION = process.env.AWS_REGION || 'us-west-2'
const PROJECT_NAME = process.env.PROJECT_NAME || 'strands-agent-chatbot'
const ENVIRONMENT = process.env.ENVIRONMENT || 'dev'

export const runtime = 'nodejs'

// Cache for memory configuration
let memoryConfigCache: {
  memoryId: string
  strategies: StrategyInfo[]
  cachedAt: number
} | null = null

const CACHE_TTL_MS = 5 * 60 * 1000 // 5 minutes

interface StrategyInfo {
  type: string
  id: string
}

/**
 * Get memory ID from environment or SSM Parameter Store
 */
async function getMemoryId(): Promise<string> {
  // First check environment variable
  const envMemoryId = process.env.MEMORY_ID
  if (envMemoryId) {
    return envMemoryId
  }

  // Fall back to SSM Parameter Store
  const ssmClient = new SSMClient({ region: AWS_REGION })
  const response = await ssmClient.send(
    new GetParameterCommand({
      Name: `/${PROJECT_NAME}/${ENVIRONMENT}/agentcore/memory-id`,
    })
  )

  if (!response.Parameter?.Value) {
    throw new Error('Memory ID not found in SSM Parameter Store')
  }

  return response.Parameter.Value
}

/**
 * Get memory strategy info by calling the backend agent
 * Uses a simple approach: hardcoded strategy name patterns that match CDK deployment
 */
async function getMemoryStrategies(memoryId: string): Promise<StrategyInfo[]> {
  // Check cache first
  if (memoryConfigCache && Date.now() - memoryConfigCache.cachedAt < CACHE_TTL_MS) {
    if (memoryConfigCache.memoryId === memoryId) {
      return memoryConfigCache.strategies
    }
  }

  // Strategy names are deterministic based on CDK deployment
  // Pattern: {strategy_name}-{random_suffix}
  // We need to list memory records to discover actual strategy IDs

  // For now, use the known strategy ID patterns from the deployed memory
  // These are discovered at runtime by listing records
  const strategies: StrategyInfo[] = [
    { type: 'USER_PREFERENCE', id: 'user_preference_extraction' },
    { type: 'SEMANTIC', id: 'semantic_fact_extraction' },
    { type: 'SUMMARIZATION', id: 'conversation_summary' },
  ]

  // Try to discover actual strategy IDs by listing records
  const client = new BedrockAgentCoreClient({ region: AWS_REGION })

  for (const strategy of strategies) {
    try {
      // Try listing with the base pattern to discover full ID
      const testNamespace = `/strategies/${strategy.id}`
      const response = await client.send(
        new ListMemoryRecordsCommand({
          memoryId,
          namespace: testNamespace,
          maxResults: 1,
        })
      )

      // If we get records, extract the full strategy ID from namespaces
      const records = response.memoryRecordSummaries || []
      if (records.length > 0 && records[0].namespaces && records[0].namespaces.length > 0) {
        const ns = records[0].namespaces[0]
        const match = ns.match(/\/strategies\/([^/]+)\//)
        if (match) {
          strategy.id = match[1]
        }
      }
    } catch {
      // Strategy might not have records yet, continue with base name
    }
  }

  // Update cache
  memoryConfigCache = {
    memoryId,
    strategies,
    cachedAt: Date.now(),
  }

  return strategies
}

/**
 * Delete all memory records in a namespace
 */
async function deleteMemoryRecordsInNamespace(
  client: BedrockAgentCoreClient,
  memoryId: string,
  namespace: string
): Promise<number> {
  let deletedCount = 0
  let nextToken: string | undefined

  do {
    // List all records in the namespace
    const listResponse = await client.send(
      new ListMemoryRecordsCommand({
        memoryId,
        namespace,
        maxResults: 100,
        nextToken,
      })
    )

    const records = listResponse.memoryRecordSummaries || []

    // Delete each record
    for (const record of records) {
      const recordId = record.memoryRecordId
      if (recordId) {
        try {
          await client.send(
            new DeleteMemoryRecordCommand({
              memoryId,
              memoryRecordId: recordId,
            })
          )
          deletedCount++
        } catch (error) {
          console.error(`[Memory] Failed to delete record ${recordId}:`, error)
        }
      }
    }

    nextToken = listResponse.nextToken
  } while (nextToken)

  return deletedCount
}

/**
 * Count records in a namespace
 */
async function countRecordsInNamespace(
  client: BedrockAgentCoreClient,
  memoryId: string,
  namespace: string
): Promise<number> {
  let count = 0
  let nextToken: string | undefined

  do {
    const listResponse = await client.send(
      new ListMemoryRecordsCommand({
        memoryId,
        namespace,
        maxResults: 100,
        nextToken,
      })
    )

    count += (listResponse.memoryRecordSummaries || []).length
    nextToken = listResponse.nextToken
  } while (nextToken)

  return count
}

export async function DELETE(request: NextRequest) {
  try {
    // Check if local mode
    if (IS_LOCAL) {
      return NextResponse.json(
        {
          success: false,
          error: 'Memory reset is only available in cloud mode',
        },
        { status: 400 }
      )
    }

    // Extract user from Cognito JWT token
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    if (userId === 'anonymous') {
      return NextResponse.json(
        {
          success: false,
          error: 'Authentication required to reset memory',
        },
        { status: 401 }
      )
    }

    // Get namespace filter from query params
    const searchParams = request.nextUrl.searchParams
    const namespaceFilter = searchParams.get('namespace') || 'all'

    console.log(`[Memory] Resetting memory for user ${userId}, namespace: ${namespaceFilter}`)

    // Get memory ID
    const memoryId = await getMemoryId()
    console.log(`[Memory] Using memory ID: ${memoryId}`)

    // Get strategy info
    const strategies = await getMemoryStrategies(memoryId)
    console.log(`[Memory] Found ${strategies.length} strategies`)

    // Initialize data plane client
    const client = new BedrockAgentCoreClient({ region: AWS_REGION })

    // Map namespace filter to strategy types
    const namespaceToType: Record<string, string> = {
      preferences: 'USER_PREFERENCE',
      facts: 'SEMANTIC',
      summaries: 'SUMMARIZATION',
    }

    // Determine which strategies to process
    let strategiesToProcess: StrategyInfo[] = []

    if (namespaceFilter === 'all') {
      strategiesToProcess = strategies
    } else {
      const targetType = namespaceToType[namespaceFilter]
      if (!targetType) {
        return NextResponse.json(
          {
            success: false,
            error: `Invalid namespace: ${namespaceFilter}. Must be one of: preferences, facts, summaries, all`,
          },
          { status: 400 }
        )
      }
      strategiesToProcess = strategies.filter((s) => s.type === targetType)
    }

    // Delete records for each strategy
    const results: Record<string, number> = {}
    let totalDeleted = 0

    for (const strategy of strategiesToProcess) {
      // Build the namespace path for this user
      // Pattern: /strategies/{strategyId}/actors/{actorId}
      const namespace = `/strategies/${strategy.id}/actors/${userId}`

      console.log(`[Memory] Deleting records from namespace: ${namespace}`)

      try {
        const deletedCount = await deleteMemoryRecordsInNamespace(client, memoryId, namespace)
        results[strategy.type] = deletedCount
        totalDeleted += deletedCount
        console.log(`[Memory] Deleted ${deletedCount} ${strategy.type} records`)
      } catch (error) {
        console.error(`[Memory] Error deleting ${strategy.type} records:`, error)
        results[strategy.type] = 0
      }
    }

    return NextResponse.json({
      success: true,
      message: `Deleted ${totalDeleted} memory records`,
      details: {
        userId,
        namespace: namespaceFilter,
        deletedCounts: results,
        totalDeleted,
      },
    })
  } catch (error) {
    console.error('[Memory] Error resetting memory:', error)

    return NextResponse.json(
      {
        success: false,
        error: 'Failed to reset memory',
        message: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    )
  }
}

/**
 * GET /api/memory/reset - Get memory stats for the user
 */
export async function GET(request: NextRequest) {
  try {
    // Check if local mode
    if (IS_LOCAL) {
      return NextResponse.json(
        {
          success: false,
          error: 'Memory stats are only available in cloud mode',
        },
        { status: 400 }
      )
    }

    // Extract user from Cognito JWT token
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    if (userId === 'anonymous') {
      return NextResponse.json(
        {
          success: false,
          error: 'Authentication required to view memory stats',
        },
        { status: 401 }
      )
    }

    console.log(`[Memory] Getting memory stats for user ${userId}`)

    // Get memory ID
    const memoryId = await getMemoryId()

    // Get strategy info
    const strategies = await getMemoryStrategies(memoryId)

    // Initialize data plane client
    const client = new BedrockAgentCoreClient({ region: AWS_REGION })

    // Count records for each strategy
    const stats: Record<string, { count: number; namespace: string }> = {}

    for (const strategy of strategies) {
      const namespace = `/strategies/${strategy.id}/actors/${userId}`

      try {
        const count = await countRecordsInNamespace(client, memoryId, namespace)
        stats[strategy.type] = { count, namespace }
      } catch (error) {
        console.error(`[Memory] Error counting ${strategy.type} records:`, error)
        stats[strategy.type] = { count: 0, namespace }
      }
    }

    const totalCount = Object.values(stats).reduce((sum, s) => sum + s.count, 0)

    return NextResponse.json({
      success: true,
      userId,
      memoryId,
      stats,
      totalRecords: totalCount,
    })
  } catch (error) {
    console.error('[Memory] Error getting memory stats:', error)

    return NextResponse.json(
      {
        success: false,
        error: 'Failed to get memory stats',
        message: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    )
  }
}
