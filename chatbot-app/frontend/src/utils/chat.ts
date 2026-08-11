import { Calculator, Globe, Code, Image, UserCheck, Monitor, GitBranch, Cog } from 'lucide-react'
import toolsConfig from '@/config/tools-config.json'

// Type for display name
interface DisplayName {
  running: string
  complete: string
}

// Build a flat map of tool id -> displayName from tools-config.json
const buildDisplayNameMap = (): Record<string, DisplayName> => {
  const map: Record<string, DisplayName> = {}

  const categories = [
    'local_tools',
    'builtin_tools',
    'browser_automation',
    'gateway_targets',
    'agentcore_runtime_a2a'
  ] as const

  for (const category of categories) {
    const tools = (toolsConfig as any)[category] as any[] | undefined
    if (!tools) continue

    for (const tool of tools) {
      // Check if tool itself has displayName
      if (tool.displayName) {
        map[tool.id] = tool.displayName
      }
      // Check sub-tools (for dynamic tools with nested tools array)
      if (tool.tools && Array.isArray(tool.tools)) {
        for (const subTool of tool.tools) {
          if (subTool.displayName) {
            map[subTool.id] = subTool.displayName
          }
        }
      }
    }
  }

  return map
}

// Pre-built map for performance
const displayNameMap = buildDisplayNameMap()

// Format a skill or tool name for display: "web-search" → "Web Search", "ddg_web_search" → "Ddg Web Search"
const formatName = (name: string): string =>
  name.replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

// Tool name to user-friendly display name
export const getToolDisplayName = (toolId: string, isComplete: boolean, toolInput?: any): string => {
  // skill_dispatcher is a meta-tool that returns SKILL.md instructions.
  if (toolId === 'skill_dispatcher') {
    return isComplete ? 'Found the right tool' : 'Finding the right tool...'
  }

  if (toolId === 'delegate_task') {
    const profile = toolInput?.profile === 'reviewer' ? 'Reviewer' : 'Analyst'
    return isComplete ? `${profile} completed` : `${profile} working...`
  }

  const mapping = displayNameMap[toolId]
  if (mapping) {
    return isComplete ? mapping.complete : mapping.running
  }
  const formatted = formatName(toolId)
  return isComplete ? `Used ${formatted}` : `Using ${formatted}`
}

export const getToolIconById = (toolId: string) => {
  switch (toolId) {
    case 'calculator':
      return Calculator
    case 'http_request':
      return Globe
    case 'code_interpreter':
      return Code
    case 'generate_image':
      return Image
    case 'image_reader':
      return Image
    case 'handoff_to_user':
      return UserCheck
    case 'browser':
      return Monitor
    case 'diagram':
      return GitBranch
    default:
      return Cog
  }
}

export const getCategoryColor = (category: string) => {
  switch (category) {
    case 'utilities':
      return 'bg-blue-500/10 text-blue-600 border-blue-500/20'
    case 'web':
      return 'bg-green-500/10 text-green-600 border-green-500/20'
    case 'code':
      return 'bg-purple-500/10 text-purple-600 border-purple-500/20'
    case 'multimodal':
      return 'bg-orange-500/10 text-orange-600 border-orange-500/20'
    case 'workflow':
      return 'bg-pink-500/10 text-pink-600 border-pink-500/20'
    case 'visualization':
      return 'bg-indigo-500/10 text-indigo-600 border-indigo-500/20'
    default:
      return 'bg-gray-500/10 text-gray-600 border-gray-500/20'
  }
}


export const detectBackendUrl = async (): Promise<{ url: string; connected: boolean }> => {
  // New architecture: BFF is integrated into Next.js as API Routes
  // Always use relative paths to /api endpoints

  try {
    const response = await fetch('/api/health', {
      method: 'GET',
      signal: AbortSignal.timeout(5000)
    })

    if (response.ok) {
      const data = await response.json()
      if (data.status === 'healthy') {
        return { url: '', connected: true } // Empty URL means use relative paths
      }
    }
  } catch (error) {
    console.error('BFF health check failed:', error)
    return { url: '', connected: false }
  }

  return { url: '', connected: false }
}
