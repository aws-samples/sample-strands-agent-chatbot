import { IconType } from 'react-icons';
import {
  TbCalculator,
  TbChartBar,
  TbChartDots,
  TbSearch,
  TbWorldWww,
  TbBrowser,
  TbCloudRain,
  TbFileText,
  TbTable,
  TbPresentation,
  TbChartLine,
  TbCode,
  TbTelescope,
  TbAnalyze,
  TbShieldCheck,
} from 'react-icons/tb';
import {
  SiDuckduckgo,
  SiGoogle,
  SiWikipedia,
  SiArxiv,
  SiGooglemaps,
  SiGmail,
  SiGooglecalendar,
  SiNotion,
  SiGithub,
} from 'react-icons/si';
import toolsConfig from './tools-config.json';

/**
 * Icon mapping for tools using react-icons
 * Uses professional brand icons where available (Simple Icons)
 * Falls back to Tabler Icons for generic tools
 */
export const toolIconMap: Record<string, IconType> = {
  // Analytics & Reports
  calculator: TbCalculator,
  create_visualization: TbChartBar,
  visual_design: TbChartDots,
  word_document_tools: TbFileText,
  excel_spreadsheet_tools: TbTable,
  powerpoint_presentation_tools: TbPresentation,
  gateway_financial_news: TbChartLine,
  'gateway_financial-news': TbChartLine,

  // Code Execution
  code_interpreter_tools: TbCode,

  // Research & Search
  ddg_web_search: SiDuckduckgo,
  gateway_google_web_search: SiGoogle,
  'gateway_google-web-search': SiGoogle,
  gateway_tavily_search: TbSearch,
  'gateway_tavily-search': TbSearch,
  gateway_wikipedia_search: SiWikipedia,
  'gateway_wikipedia-search': SiWikipedia,
  gateway_arxiv_search: SiArxiv,
  'gateway_arxiv-search': SiArxiv,
  fetch_url_content: TbWorldWww,

  // Web & Automation
  browser_automation: TbBrowser,

  // Location & Live Data
  gateway_google_maps: SiGooglemaps,
  'gateway_google-maps': SiGooglemaps,
  gateway_show_on_map: SiGooglemaps,
  gateway_weather: TbCloudRain,
  get_current_weather: TbCloudRain,

  // Productivity (MCP)
  mcp_gmail: SiGmail,
  mcp_calendar: SiGooglecalendar,
  mcp_notion: SiNotion,
  mcp_github: SiGithub,

  // Research Agent
  'agentcore_research-agent': TbTelescope,
  research_agent: TbTelescope,

  // Delegated specialists
  delegation_analyst: TbAnalyze,
  delegation_reviewer: TbShieldCheck,
};

/**
 * Image-based icons for tools that have actual logo files in /public/tool-icons/
 * These take priority over react-icon components when available.
 */
export const toolImageMap: Record<string, string> = {
  'mcp_gmail': '/tool-icons/gmail.svg',
  'mcp_calendar': '/tool-icons/google-calendar.svg',
  'mcp_notion': '/tool-icons/notion.svg',
  'mcp_github': '/tool-icons/github.svg',
  'calculator': '/tool-icons/calculator.svg',
  'excel_spreadsheet_tools': '/tool-icons/excel.svg',
  'visual_design': '/tool-icons/diagram.svg',
  'gateway_arxiv_search': '/tool-icons/arxiv.svg',
  'gateway_arxiv-search': '/tool-icons/arxiv.svg',
  'gateway_google_maps': '/tool-icons/google-maps.svg',
  'gateway_google-maps': '/tool-icons/google-maps.svg',
  'gateway_show_on_map': '/tool-icons/google-maps.svg',
  'gateway_google_web_search': '/tool-icons/google-search.svg',
  'gateway_google-web-search': '/tool-icons/google-search.svg',
  'gateway_google_image_search': '/tool-icons/google-search.svg',
  'browser_automation': '/tool-icons/nova-act.png',
  'powerpoint_presentation_tools': '/tool-icons/powerpoint.svg',
  'word_document_tools': '/tool-icons/word.svg',
  'fetch_url_content': '/tool-icons/url-fetcher.svg',
  'create_visualization': '/tool-icons/visualization.svg',
  'gateway_tavily_search': '/tool-icons/tavily.png',
  'gateway_tavily-search': '/tool-icons/tavily.png',
  'gateway_tavily_extract': '/tool-icons/tavily.png',
  'ddg_web_search': '/tool-icons/duckduckgo.svg',
  'gateway_weather': '/tool-icons/weather.png',
  'get_current_weather': '/tool-icons/weather.png',
  'gateway_wikipedia_search': '/tool-icons/wikipedia.svg',
  'gateway_wikipedia-search': '/tool-icons/wikipedia.svg',
  'gateway_financial_news': '/tool-icons/financial.svg',
  'gateway_financial-news': '/tool-icons/financial.svg',
  'code_interpreter_tools': '/tool-icons/code-interpreter.svg',
  'agentcore_code-agent': '/tool-icons/code-agent.svg',
  'code_agent': '/tool-icons/code-agent.svg',
  'workspace_tools': '/tool-icons/s3-workspace.svg',
  'create_excalidraw_diagram': '/tool-icons/excalidraw.svg',
  'excalidraw': '/tool-icons/excalidraw.svg',
};

/**
 * Sub-tool ID → parent group ID mapping, built from tools-config.json.
 * e.g. "create_word_document" → "word_document_tools"
 */
const subToolToParent: Record<string, string> = {};
const allGroups = [
  ...toolsConfig.local_tools,
  ...toolsConfig.builtin_tools,
  ...toolsConfig.browser_automation,
  ...toolsConfig.gateway_targets,
  ...toolsConfig.agentcore_runtime_a2a,
  ...toolsConfig.agentcore_runtime_mcp,
];
for (const group of allGroups) {
  if ('tools' in group && Array.isArray((group as any).tools)) {
    for (const sub of (group as any).tools) {
      subToolToParent[sub.id] = group.id;

      // MCP/Gateway tools: backend may strip the prefix (e.g., "mcp_notion_search" → "notion_search")
      // Also map the unprefixed version so icons resolve correctly.
      for (const prefix of ['mcp_', 'gateway_']) {
        if (sub.id.startsWith(prefix)) {
          const unprefixed = sub.id.slice(prefix.length);
          if (!(unprefixed in subToolToParent)) {
            subToolToParent[unprefixed] = group.id;
          }
        }
      }
    }
  }
}

/**
 * Resolve a tool ID to the one that has an icon mapping.
 * Checks direct match first, then falls back to parent group ID.
 */
function resolveIconId(toolId: string, map: Record<string, any>): string | null {
  if (toolId in map) return toolId;
  const parentId = subToolToParent[toolId];
  if (parentId && parentId in map) return parentId;
  return null;
}

/**
 * Skill name → representative tool ID mapping.
 * Used to resolve icons for skill_dispatcher.
 */
export const skillToToolId: Record<string, string> = {
  'web-search': 'ddg_web_search',
  'url-fetcher': 'fetch_url_content',
  'visualization': 'create_visualization',
  'visual-design': 'visual_design',
  'word-documents': 'word_document_tools',
  'excel-spreadsheets': 'excel_spreadsheet_tools',
  'powerpoint-presentations': 'powerpoint_presentation_tools',
  'browser-automation': 'browser_automation',
  'code-interpreter': 'code_interpreter_tools',
  'weather': 'gateway_weather',
  'financial-news': 'gateway_financial-news',
  'arxiv-search': 'gateway_arxiv-search',
  'google-web-search': 'gateway_google-web-search',
  'google-maps': 'gateway_google-maps',
  'wikipedia-search': 'gateway_wikipedia-search',
  'tavily-search': 'gateway_tavily-search',
  'gmail': 'mcp_gmail',
  'google-calendar': 'mcp_calendar',
  'notion': 'mcp_notion',
  'github': 'mcp_github',
  'excalidraw': 'create_excalidraw_diagram',
  'workspace': 'workspace_tools',
  'code-agent': 'agentcore_code-agent',
  'research-agent': 'agentcore_research-agent',
  'doc-coauthoring': 'word_document_tools',
};

/**
 * Resolve the effective tool ID for icon lookup.
 * skill_dispatcher is a meta-tool (returns SKILL.md instructions); map it to
 * a representative icon via skill_name. Other tools pass through — the SSE
 * formatter already unwraps skill_executor to the inner tool name.
 */
export function resolveEffectiveToolId(toolId: string, toolInput?: any): string {
  if (toolId === 'skill_dispatcher' && toolInput?.skill_name) {
    return skillToToolId[toolInput.skill_name] || toolId;
  }
  if (toolId === 'delegate_task') {
    return toolInput?.profile === 'reviewer'
      ? 'delegation_reviewer'
      : 'delegation_analyst';
  }
  return toolId;
}

/**
 * Get the image path for a tool ID, if one exists.
 * Returns null if the tool should use a react-icon instead.
 */
export function getToolImageSrc(toolId: string): string | null {
  const resolved = resolveIconId(toolId, toolImageMap);
  return resolved ? toolImageMap[resolved] : null;
}

/**
 * Get the icon component for a tool ID
 * Returns a default icon if tool ID is not found
 */
export function getToolIcon(toolId: string): IconType {
  const resolved = resolveIconId(toolId, toolIconMap);
  return resolved ? toolIconMap[resolved] : TbSearch;
}
