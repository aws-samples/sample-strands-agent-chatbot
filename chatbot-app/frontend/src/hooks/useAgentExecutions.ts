import { useState, useMemo, useEffect } from 'react'
import type { ResearchJob } from '@/lib/research-jobs'
import { parseResearchStartReceipt } from '@/lib/research-start-receipt'

export interface ResearchExecutionData {
  query: string
  result: string
  status: 'idle' | 'searching' | 'analyzing' | 'generating' | 'complete' | 'error' | 'declined'
  agentName: string
}

interface MessageGroup {
  type: 'user' | 'assistant_turn'
  messages: Array<{
    toolExecutions?: Array<{
      id: string
      toolName: string
      toolInput?: Record<string, any>
      toolResult?: string
      streamingResponse?: string
      isComplete?: boolean
      isCancelled?: boolean
    }>
  }>
}

export function useAgentExecutions(
  groupedMessages: MessageGroup[],
  researchJobs: ResearchJob[] = [],
) {
  const [researchData, setResearchData] = useState<Map<string, ResearchExecutionData>>(new Map())

  const computedResearchData = useMemo(() => {
    const newResearchData = new Map<string, ResearchExecutionData>()

    for (const group of groupedMessages) {
      if (group.type === 'assistant_turn') {
        for (const message of group.messages) {
          const toolExecutions = message.toolExecutions
          if (!toolExecutions || toolExecutions.length === 0) continue

          for (const execution of toolExecutions) {
            if (execution.toolName === 'research_agent') {
              const executionId = execution.id
              const query = execution.toolInput?.plan || "Research Task"
              const started = parseResearchStartReceipt(execution.toolResult)
              const job = started
                ? researchJobs.find(item => item.jobId === started.job_id)
                : researchJobs.find(item => item.artifactId === `research-${executionId}`)

              if (!execution.isComplete || started) {
                const isError = job?.status === 'error'
                const isComplete = job && ['completed', 'delivering', 'delivered'].includes(job.status)
                const result = typeof job?.artifact?.content === 'string'
                  ? job.artifact.content
                  : execution.streamingResponse || ''
                newResearchData.set(executionId, {
                  query,
                  result,
                  status: isError ? 'error' : isComplete ? 'complete' : result ? 'generating' : 'searching',
                  agentName: 'Research Agent'
                })
              } else if (execution.toolResult) {
                const resultText = execution.toolResult.toLowerCase()
                const isError = execution.isCancelled || resultText.includes('error:') || resultText.includes('failed:')
                const isDeclined = resultText === 'user declined to proceed with research' ||
                                  resultText === 'user declined to proceed with browser automation'

                let status: 'complete' | 'error' | 'declined' = 'complete'
                if (isError) status = 'error'
                else if (isDeclined) status = 'declined'

                newResearchData.set(executionId, {
                  query,
                  result: execution.toolResult,
                  status,
                  agentName: 'Research Agent'
                })
              }
            }
          }
        }
      }
    }

    return newResearchData
  }, [groupedMessages, researchJobs])

  useEffect(() => {
    setResearchData(prev => {
      if (computedResearchData.size !== prev.size ||
          Array.from(computedResearchData.entries()).some(([id, data]) => {
            const existing = prev.get(id)
            return !existing || existing.result !== data.result || existing.status !== data.status
          })) {
        return computedResearchData
      }
      return prev
    })
  }, [computedResearchData])

  return { researchData }
}
