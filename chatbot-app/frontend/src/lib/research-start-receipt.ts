export interface ResearchStartReceipt {
  status: 'started'
  job_id: string
  artifact_id: string
}

export function parseResearchStartReceipt(result: unknown): ResearchStartReceipt | null {
  if (typeof result !== 'string' || !result) return null
  try {
    const parsed = JSON.parse(result)
    if (
      parsed?.status === 'started' &&
      typeof parsed.job_id === 'string' &&
      parsed.job_id &&
      typeof parsed.artifact_id === 'string' &&
      parsed.artifact_id
    ) {
      return parsed as ResearchStartReceipt
    }
  } catch {
    // Tool results from older research flows are not start receipts.
  }
  return null
}

export function hideBackgroundResearchInputs<T extends {
  role?: string
  content?: Array<{ text?: string }>
  startsNewAssistantTurn?: boolean
}>(messages: T[]): T[] {
  let pendingDeliveryBoundary = false
  const visible: T[] = []

  for (const message of messages) {
    const isBackgroundInput =
      message.role === 'user' &&
      Array.isArray(message.content) &&
      message.content.some(part =>
        typeof part?.text === 'string' &&
        part.text.includes('<background-research-result '),
      )

    if (isBackgroundInput) {
      pendingDeliveryBoundary = true
      continue
    }

    if (pendingDeliveryBoundary && message.role === 'assistant') {
      visible.push({ ...message, startsNewAssistantTurn: true })
      pendingDeliveryBoundary = false
      continue
    }

    if (message.role === 'user') pendingDeliveryBoundary = false
    visible.push(message)
  }

  return visible
}
