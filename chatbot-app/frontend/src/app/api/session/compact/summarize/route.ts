/**
 * Session Compact - Generate summary from conversation messages
 *
 * Receives the current messages directly from the frontend (no need to
 * re-load from AgentCore Memory, which avoids actorId / payload format issues).
 * Generates a summary with GPT-5.6 Luna via Bedrock Mantle.
 */
import { NextRequest, NextResponse } from 'next/server'

const COMPACTION_MODEL_ID = 'openai.gpt-5.6-luna'
const MANTLE_RESPONSES_URL = 'https://bedrock-mantle.us-east-1.api.aws/openai/v1/responses'

export const runtime = 'nodejs'

/**
 * Build a plain-text transcript from UI messages.
 * Handles both API format (role/content) and UI format (sender/text).
 */
function buildTranscript(messages: any[]): string {
  const lines: string[] = []
  for (const msg of messages) {
    const role = (msg.role === 'user' || msg.sender === 'user') ? 'User' : 'Assistant'
    const content = Array.isArray(msg.content)
      ? msg.content.filter((c: any) => c.text).map((c: any) => c.text).join('\n')
      : typeof msg.content === 'string' ? msg.content
      : typeof msg.text === 'string' ? msg.text
      : ''
    if (content.trim()) {
      lines.push(`${role}: ${content.trim()}`)
    }
  }
  return lines.join('\n\n')
}

const MAX_TRANSCRIPT_CHARS = 200_000

function truncateTranscript(messages: any[], maxChars: number): { transcript: string; truncated: boolean } {
  const full = buildTranscript(messages)
  if (full.length <= maxChars) {
    return { transcript: full, truncated: false }
  }

  for (let i = 1; i < messages.length; i++) {
    const trimmed = buildTranscript(messages.slice(i))
    if (trimmed.length <= maxChars) {
      return { transcript: trimmed, truncated: true }
    }
  }

  return { transcript: full.slice(full.length - maxChars), truncated: true }
}

function buildPrompt(transcript: string, truncated: boolean): string {
  const truncationNote = truncated
    ? 'Note: The conversation was very long. Only the most recent portion is included below.\n\n'
    : ''
  return `Your task is to create a detailed, structured summary of the conversation below. This summary will replace the original messages to keep context manageable, so it must capture all essential information needed to continue the work seamlessly.

Before writing your final summary, briefly organize your thoughts inside <analysis> tags:
1. Walk through the conversation chronologically and identify each user request, your response, and the outcome.
2. Note any files, tools, artifacts, or technical details mentioned.
3. Identify what was completed vs. what is still pending.

Then write your summary using the sections below. Omit any section that has no relevant content.

<sections>
1. **Primary Request and Intent**
   What the user explicitly asked for, including follow-up refinements.

2. **Key Decisions and Outcomes**
   Important decisions made, approaches chosen, and results delivered.

3. **Tools and Artifacts**
   Tools invoked (web search, code interpreter, documents, etc.), files created or modified, and key outputs.
   Include file names, artifact titles, or resource identifiers where applicable.

4. **Technical Details**
   Specific technical concepts, configurations, code patterns, or data referenced in the conversation.
   Include enough detail (e.g., parameter values, code snippets, API names) so context is not lost.

5. **Problems Solved**
   Bugs fixed, errors resolved, or troubleshooting steps taken.

6. **Pending Tasks**
   Any unfinished work the user explicitly asked for, or next steps that were agreed upon but not yet completed.

7. **Current Work**
   What was being worked on immediately before this summary, including the most recent user message and assistant action. Be precise — this is the continuation point.
</sections>

Guidelines:
- Be thorough on technical details but concise in prose. Aim for 500–1500 words depending on conversation length.
- Preserve specific names, values, and identifiers — do not generalize them away.
- If the conversation is non-technical (casual Q&A, general knowledge), keep the summary brief and skip technical sections.

${truncationNote}Conversation:
${transcript}

Now produce the summary following the instructions above.`
}

type MantleContent = {
  type?: string
  text?: string
}

type MantleOutput = {
  type?: string
  content?: MantleContent[]
}

type MantleResponse = {
  output?: MantleOutput[]
  error?: { message?: string }
}

async function generateSummary(prompt: string): Promise<string> {
  const apiKey = process.env.AWS_BEARER_TOKEN_BEDROCK
  if (!apiKey) {
    throw new Error('AWS_BEARER_TOKEN_BEDROCK is not configured')
  }

  const response = await fetch(MANTLE_RESPONSES_URL, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: COMPACTION_MODEL_ID,
      input: prompt,
      max_output_tokens: 4096,
    }),
  })

  const payload = await response.json() as MantleResponse
  if (!response.ok) {
    throw new Error(payload.error?.message || `Mantle Responses API returned ${response.status}`)
  }

  const text = payload.output
    ?.filter(item => item.type === 'message')
    .flatMap(item => item.content ?? [])
    .filter(item => item.type === 'output_text' && item.text)
    .map(item => item.text)
    .join('') ?? ''

  if (!text) {
    throw new Error('Mantle Responses API returned no summary text')
  }
  return text
}

function isContextWindowError(error: unknown): boolean {
  const msg = error instanceof Error ? error.message : String(error)
  return (
    msg.includes('too long') ||
    msg.includes('context length') ||
    msg.includes('context window') ||
    msg.includes('input is too') ||
    msg.includes('ValidationException') ||
    msg.includes('maximum context')
  )
}

export async function POST(request: NextRequest) {
  try {
    const { messages } = await request.json()

    if (!messages || !Array.isArray(messages)) {
      return NextResponse.json(
        { success: false, error: 'messages array is required' },
        { status: 400 }
      )
    }

    // Filter to only user/assistant text messages (skip tool-only turns), then take the most recent 40.
    // Tool input/result pairs are excluded: UI messages with only toolExecutions have empty text,
    // and API-format content arrays are filtered to text blocks only in buildTranscript.
    const MAX_MESSAGES = 40
    const allTextMessages = messages.filter((msg: any) => {
      const sender = msg.sender || msg.role
      return (sender === 'user' || sender === 'assistant' || sender === 'bot') &&
        !msg.isToolMessage &&
        (msg.text || msg.content)
    })
    const recent = allTextMessages.slice(-MAX_MESSAGES)
    // Always include the first user message for context anchoring
    const firstUser = allTextMessages.find((m: any) => (m.sender || m.role) === 'user')
    const textMessages = firstUser && !recent.includes(firstUser)
      ? [firstUser, ...recent]
      : recent

    if (textMessages.length === 0) {
      return NextResponse.json(
        { success: false, error: 'No messages to summarize' },
        { status: 400 }
      )
    }

    console.log(`[compact/summarize] Summarizing ${textMessages.length} messages (max ${MAX_MESSAGES}+first, original ${messages.length})`)

    const { transcript, truncated } = truncateTranscript(textMessages, MAX_TRANSCRIPT_CHARS)
    if (truncated) {
      console.warn(`[compact/summarize] Transcript truncated to ${transcript.length} chars`)
    }

    let summary: string
    try {
      summary = await generateSummary(buildPrompt(transcript, truncated))
    } catch (firstError) {
      if (!isContextWindowError(firstError)) throw firstError

      console.warn(`[compact/summarize] Context window error, retrying with reduced transcript`)
      const { transcript: shorter, truncated: moreTruncated } = truncateTranscript(textMessages, 100_000)
      summary = await generateSummary(buildPrompt(shorter, moreTruncated))
    }

    return new Response(summary, {
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    })
  } catch (error) {
    console.error('[compact/summarize] Error:', error)
    return NextResponse.json(
      {
        success: false,
        error: 'Failed to generate summary',
        message: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    )
  }
}
