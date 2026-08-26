/**
 * Available Models endpoint - returns list of supported AI models
 *
 * Three backend execution paths (decided server-side by model_id):
 * - Bedrock Runtime Converse (BedrockModel): native/cross-Region profiles.
 * - Bedrock Runtime Responses (OpenAIResponsesModel): GPT-5.6.
 * - Bedrock Mantle Responses (OpenAIResponsesModel): Gemma 4.
 */
import { NextResponse } from 'next/server'

export const runtime = 'nodejs'

const AVAILABLE_MODELS = [
  // Claude (Anthropic) - native Bedrock, prompt caching
  {
    id: 'us.anthropic.claude-opus-5',
    name: 'Claude Opus 5',
    provider: 'Anthropic',
    description: 'Most intelligent model, best for complex tasks',
    noTemperature: true
  },
  {
    id: 'us.anthropic.claude-sonnet-5',
    name: 'Claude Sonnet 5',
    provider: 'Anthropic',
    description: 'Most capable model, balanced performance',
    noTemperature: true
  },
  {
    id: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
    name: 'Claude Haiku 4.5',
    provider: 'Anthropic',
    description: 'Fast and efficient, cost-effective'
  },

  // GPT (OpenAI) - Bedrock Runtime Responses with cross-Region profiles
  {
    id: 'us.openai.gpt-5.6-sol',
    name: 'GPT-5.6 Sol',
    provider: 'OpenAI',
    description: 'Flagship frontier model via Bedrock Runtime',
    noTemperature: true
  },
  {
    id: 'us.openai.gpt-5.6-terra',
    name: 'GPT-5.6 Terra',
    provider: 'OpenAI',
    description: 'Balanced frontier model via Bedrock Runtime',
    noTemperature: true
  },
  {
    id: 'us.openai.gpt-5.6-luna',
    name: 'GPT-5.6 Luna',
    provider: 'OpenAI',
    description: 'Fast and cost-efficient model via Bedrock Runtime',
    noTemperature: true
  },
  {
    id: 'openai.gpt-oss-120b-1:0',
    name: 'GPT OSS 120B',
    provider: 'OpenAI',
    description: 'Open-source GPT model with 120B parameters'
  },

  // Grok (xAI) - Bedrock Runtime cross-Region inference profile
  {
    id: 'us.xai.grok-4.6',
    name: 'Grok 4.6',
    provider: 'xAI',
    description: 'Advanced reasoning model via Bedrock Runtime'
  },

  // DeepSeek - native Bedrock
  {
    id: 'deepseek.v3.2',
    name: 'DeepSeek V3.2',
    provider: 'DeepSeek',
    description: 'Advanced language model with strong reasoning capabilities'
  },

  // Gemma (Google) - gemma-4 via Mantle
  {
    id: 'google.gemma-4-31b',
    name: 'Gemma 4 31B',
    provider: 'Google',
    description: 'Latest multimodal model (via Bedrock Mantle)'
  },
  {
    id: 'google.gemma-4-26b-a4b',
    name: 'Gemma 4 26B',
    provider: 'Google',
    description: 'Efficient MoE multimodal model (via Bedrock Mantle)'
  },

  // Z.AI GLM - native Bedrock
  {
    id: 'zai.glm-5',
    name: 'GLM-5',
    provider: 'Z.AI',
    description: 'Latest flagship reasoning and agentic model'
  },
  {
    id: 'zai.glm-4.7',
    name: 'GLM-4.7',
    provider: 'Z.AI',
    description: 'Strong general-purpose model'
  },

  // Moonshot - native Bedrock
  {
    id: 'moonshotai.kimi-k2.5',
    name: 'Kimi K2.5',
    provider: 'Moonshot AI',
    description: 'Deep reasoning model for complex workflows'
  },

  // MiniMax - native Bedrock
  {
    id: 'minimax.minimax-m2.5',
    name: 'MiniMax M2.5',
    provider: 'MiniMax AI',
    description: 'Built for coding agents and automation'
  },

  // Qwen - native Bedrock
  {
    id: 'qwen.qwen3-235b-a22b-2507-v1:0',
    name: 'Qwen 235B',
    provider: 'Qwen',
    description: 'Large-scale language model with 235B parameters'
  },

  // Mistral - native Bedrock
  {
    id: 'mistral.mistral-large-3-675b-instruct',
    name: 'Mistral Large 3',
    provider: 'Mistral AI',
    description: 'Flagship instruction model with 675B parameters'
  },

  // NVIDIA - native Bedrock
  {
    id: 'nvidia.nemotron-super-3-120b',
    name: 'Nemotron Super 3 120B',
    provider: 'NVIDIA',
    description: 'High-performance reasoning and agentic model'
  }
]

export async function GET() {
  try {
    return NextResponse.json({
      models: AVAILABLE_MODELS
    })
  } catch (error) {
    console.error('[API] Error loading available models:', error)

    return NextResponse.json({
      models: AVAILABLE_MODELS
    })
  }
}
