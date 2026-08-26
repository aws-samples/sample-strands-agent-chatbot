import { describe, expect, it } from 'vitest'

import { DEFAULT_MODEL_ID, normalizeModelId } from '@/lib/model-ids'

describe('model ID normalization', () => {
  it('uses the Bedrock Runtime inference profile as the default', () => {
    expect(DEFAULT_MODEL_ID).toBe('us.openai.gpt-5.6-terra')
  })

  it.each([
    ['openai.gpt-5.6-sol', 'us.openai.gpt-5.6-sol'],
    ['openai.gpt-5.6-terra', 'us.openai.gpt-5.6-terra'],
    ['openai.gpt-5.6-luna', 'us.openai.gpt-5.6-luna'],
    ['xai.grok-4.3', 'us.xai.grok-4.6'],
    ['xai.grok-4.6', 'us.xai.grok-4.6'],
  ])('maps %s to %s', (legacyId, canonicalId) => {
    expect(normalizeModelId(legacyId)).toBe(canonicalId)
  })

  it('preserves already canonical and unrelated IDs', () => {
    expect(normalizeModelId('us.xai.grok-4.6')).toBe('us.xai.grok-4.6')
    expect(normalizeModelId('us.anthropic.claude-sonnet-5'))
      .toBe('us.anthropic.claude-sonnet-5')
  })
})
