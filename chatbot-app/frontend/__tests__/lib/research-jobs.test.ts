import { describe, expect, it } from 'vitest'

import {
  hideBackgroundResearchInputs,
  parseResearchStartReceipt,
} from '@/lib/research-start-receipt'

describe('parseResearchStartReceipt', () => {
  it('accepts a durable background research receipt', () => {
    expect(parseResearchStartReceipt(JSON.stringify({
      status: 'started',
      job_id: 'job-1',
      artifact_id: 'research-tool-1',
    }))).toEqual({
      status: 'started',
      job_id: 'job-1',
      artifact_id: 'research-tool-1',
    })
  })

  it('ignores tool use before the background job receipt exists', () => {
    expect(parseResearchStartReceipt(undefined)).toBeNull()
    expect(parseResearchStartReceipt('legacy report body')).toBeNull()
  })
})

describe('hideBackgroundResearchInputs', () => {
  it.each([
    '<background-research-result>',
    '<background-research-result job_id="legacy-job">',
  ])('moves the hidden wake-up boundary to the delivery response for %s', tag => {
    const messages = hideBackgroundResearchInputs([
      { role: 'assistant', content: [{ text: 'Research started.' }] },
      {
        role: 'user',
        content: [{ text: tag }],
      },
      { role: 'assistant', content: [{ text: 'Research complete.' }] },
    ])

    expect(messages).toHaveLength(2)
    expect(messages[1]).toMatchObject({
      role: 'assistant',
      startsNewAssistantTurn: true,
    })
  })
})
