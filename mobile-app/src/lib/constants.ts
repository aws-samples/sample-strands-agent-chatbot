export const API_BASE_URL = (
  (process.env.EXPO_PUBLIC_API_URL as string | undefined) ?? 'http://localhost:3000'
).replace(/\/$/, '')

export const DEFAULT_MODEL_ID = 'openai.gpt-5.6-terra'
export const DEFAULT_TEMPERATURE = 0.7
export const TEXT_BUFFER_FLUSH_MS = 120

export const ENDPOINTS = {
  chat: '/api/stream/chat',
  stop: '/api/stream/stop',
  sessionNew: '/api/session/new',
  sessionList: '/api/session/list',
  sessionDelete: '/api/session/delete',
  sessionById: (id: string) => `/api/session/${encodeURIComponent(id)}`,
  conversationHistory: (id: string) => `/api/conversation/history?session_id=${encodeURIComponent(id)}`,
  streamResume: (executionId: string) => `/api/stream/resume?executionId=${encodeURIComponent(executionId)}&cursor=0`,
  health: '/api/health',
  workspaceFiles: (docType: string) => `/api/workspace/files?docType=${encodeURIComponent(docType)}`,
  s3PresignedUrl: '/api/s3/presigned-url',
  codeAgentDownload: (sessionId: string) =>
    `/api/code-agent/workspace-download?sessionId=${encodeURIComponent(sessionId)}`,
}

export interface ModelInfo {
  id: string
  name: string
  provider: string
  description: string
}

export const AVAILABLE_MODELS: ModelInfo[] = [
  { id: 'us.anthropic.claude-opus-5', name: 'Claude Opus 5', provider: 'Anthropic', description: 'Most intelligent model' },
  { id: 'us.anthropic.claude-sonnet-5', name: 'Claude Sonnet 5', provider: 'Anthropic', description: 'Balanced performance' },
  { id: 'us.anthropic.claude-haiku-4-5-20251001-v1:0', name: 'Claude Haiku 4.5', provider: 'Anthropic', description: 'Fast and efficient' },
  { id: 'openai.gpt-5.6-sol', name: 'GPT-5.6 Sol', provider: 'OpenAI', description: 'Flagship frontier model' },
  { id: 'openai.gpt-5.6-terra', name: 'GPT-5.6 Terra', provider: 'OpenAI', description: 'Balanced frontier model' },
  { id: 'openai.gpt-5.6-luna', name: 'GPT-5.6 Luna', provider: 'OpenAI', description: 'Fast and cost-efficient model' },
  { id: 'xai.grok-4.3', name: 'Grok 4.3', provider: 'xAI', description: 'Advanced reasoning model' },
  { id: 'google.gemma-4-31b', name: 'Gemma 4 31B', provider: 'Google', description: 'Latest multimodal model' },
  { id: 'deepseek.v3.2', name: 'DeepSeek V3.2', provider: 'DeepSeek', description: 'Strong reasoning capabilities' },
  { id: 'zai.glm-5', name: 'GLM-5', provider: 'Z.AI', description: 'Flagship reasoning model' },
  { id: 'moonshotai.kimi-k2.5', name: 'Kimi K2.5', provider: 'Moonshot AI', description: 'Deep reasoning model' },
]

export const MODEL_STORAGE_KEY = 'selected_model_id'
