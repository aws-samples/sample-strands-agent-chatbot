/**
 * Document Tool Configuration
 *
 * Centralized mapping of tool names to document types.
 * Used by:
 * - useStreamEvents.ts (frontend workspace file detection)
 * - /api/workspace/files/route.ts (BFF S3 lookup)
 * - /api/documents/download/route.ts (file download)
 *
 * This is derived from tools-config.json but kept as a separate
 * constant for runtime efficiency (no JSON parsing needed).
 */

export type DocumentType = 'word' | 'excel' | 'powerpoint' | 'diagram' | 'code-output' | 'image'

/**
 * Maps tool names to document types.
 *
 * Tool names come from tools-config.json:
 * - word_document_tools.tools[].id
 * - excel_spreadsheet_tools.tools[].id
 * - powerpoint_presentation_tools.tools[].id
 */
export const TOOL_TO_DOC_TYPE: Record<string, DocumentType> = {
  // Word tools (from word_document_tools)
  'create_word_document': 'word',
  'modify_word_document': 'word',
  'read_word_document': 'word',
  'list_my_word_documents': 'word',

  // Excel tools (from excel_spreadsheet_tools)
  'create_excel_spreadsheet': 'excel',
  'modify_excel_spreadsheet': 'excel',
  'read_excel_spreadsheet': 'excel',
  'list_my_excel_spreadsheets': 'excel',
  'preview_excel_sheets': 'excel',

  // PowerPoint tools (from powerpoint_presentation_tools)
  'create_presentation': 'powerpoint',
  'update_slide_content': 'powerpoint',
  'add_slide': 'powerpoint',
  'delete_slides': 'powerpoint',
  'move_slide': 'powerpoint',
  'duplicate_slide': 'powerpoint',
  'update_slide_notes': 'powerpoint',
  'analyze_presentation': 'powerpoint',
  'get_presentation_layouts': 'powerpoint',
  'list_my_powerpoint_presentations': 'powerpoint',
  'preview_presentation_slides': 'powerpoint',

  // Diagram tools (from diagram_tool)
  'generate_chart': 'diagram',
  'create_visual_design': 'diagram',

  // Browser tools (screenshots saved as images)
  'browser_save_screenshot': 'image',
}

/**
 * Maps document types to tool_type format used in download API.
 * This format matches the workspace_manager's expected tool_type.
 */
export const DOC_TYPE_TO_TOOL_TYPE: Record<DocumentType, string> = {
  'word': 'word_document',
  'excel': 'excel_spreadsheet',
  'powerpoint': 'powerpoint_presentation',
  'diagram': 'diagram',
  'code-output': 'code_output',
  'image': 'image',
}

/**
 * Maps tool_type back to document type.
 * Used for download path construction.
 */
export const TOOL_TYPE_TO_DOC_TYPE: Record<string, DocumentType> = {
  'word_document': 'word',
  'excel_spreadsheet': 'excel',
  'powerpoint_presentation': 'powerpoint',
  'diagram': 'diagram',
  'code_output': 'code-output',
  'image': 'image',
}

/**
 * Get document type for a tool name.
 */
export function getDocTypeForTool(toolName: string): DocumentType | undefined {
  return TOOL_TO_DOC_TYPE[toolName]
}

/**
 * Get tool_type format for API use.
 */
export function getToolTypeForDocType(docType: DocumentType): string {
  return DOC_TYPE_TO_TOOL_TYPE[docType]
}

/**
 * Check if a tool is a document tool.
 */
export function isDocumentTool(toolName: string): boolean {
  return toolName in TOOL_TO_DOC_TYPE
}

/**
 * Get all tool names for a specific document type.
 */
export function getToolsForDocType(docType: DocumentType): string[] {
  return Object.entries(TOOL_TO_DOC_TYPE)
    .filter(([_, type]) => type === docType)
    .map(([toolName]) => toolName)
}
