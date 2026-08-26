export const DEFAULT_MODEL_ID = 'us.openai.gpt-5.6-terra'

const MODEL_ID_ALIASES: Record<string, string> = {
  'openai.gpt-5.6-sol': 'us.openai.gpt-5.6-sol',
  'openai.gpt-5.6-terra': 'us.openai.gpt-5.6-terra',
  'openai.gpt-5.6-luna': 'us.openai.gpt-5.6-luna',
  'xai.grok-4.3': 'us.xai.grok-4.6',
  'xai.grok-4.6': 'us.xai.grok-4.6',
}

export function normalizeModelId(modelId: string): string {
  return MODEL_ID_ALIASES[modelId] ?? modelId
}
