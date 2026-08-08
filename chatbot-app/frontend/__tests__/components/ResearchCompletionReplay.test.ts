/**
 * Research completion is now driven by durable jobs rather than replaying tool
 * results from message history. These source-level contract tests guard the
 * dedupe that prevents a polling tick or page reload from reopening an artifact.
 */
import { describe, expect, it } from 'vitest'

async function readChatInterfaceSource(): Promise<string> {
  const { readFileSync } = await import('node:fs')
  const { resolve } = await import('node:path')
  return readFileSync(resolve(process.cwd(), 'src/components/ChatInterface.tsx'), 'utf8')
}

describe('durable research completion replay', () => {
  it('uses the durable research job hook as the artifact source', async () => {
    const source = await readChatInterfaceSource()

    expect(source).toContain('useResearchJobs(')
    expect(source).toContain("tool.toolName === 'research_agent'")
    expect(source).toContain('invocationIds.add(tool.id)')
    expect(source).not.toContain('Clean up when research completes')
  })

  it('deduplicates unchanged artifact versions before updating Canvas', async () => {
    const source = await readChatInterfaceSource()
    const effect = source.slice(
      source.indexOf('A completed report is readable'),
      source.indexOf('Durable session projections are the generic delivery signal'),
    )

    expect(effect).toContain('researchArtifactVersionsRef')
    expect(effect).toContain(
      'researchArtifactVersionsRef.current.get(artifact.id) === version',
    )
    expect(effect.indexOf('researchArtifactVersionsRef.current.get(artifact.id) === version'))
      .toBeLessThan(effect.indexOf('addArtifact({'))
  })

  it('clears artifact replay state when the session changes', async () => {
    const source = await readChatInterfaceSource()
    const reset = source.slice(
      source.indexOf('reloadedDeliveriesRef.current.clear()'),
      source.indexOf('}, [sessionId])'),
    )

    expect(reset).toContain('researchArtifactVersionsRef.current.clear()')
  })

  it('replays background delivery from generic durable session events', async () => {
    const source = await readChatInterfaceSource()
    const effect = source.slice(
      source.indexOf('Durable session projections are the generic delivery signal'),
      source.indexOf('// Keep reloadFromStorage ref'),
    )

    expect(source).toContain('useSessionEvents(sessionId)')
    expect(effect).toContain("event.eventType === 'assistant.turn.completed'")
    expect(effect).toContain('event.payload.executionId,')
    expect(effect).toContain('event.payload.logicalMessageId')
    expect(effect).toContain('representedOriginEventIds.has(event.originEventId)')
    expect(effect).toContain('isLoadingMessages')
    expect(effect).toContain('for (const event of unseen)')
    expect(effect).toContain('if (!replayed) break')
    expect(effect).toContain('reloadedDeliveriesRef.current.delete(event.eventId)')
    expect(effect).toContain('queuedMessages.length > 0')
    expect(effect).toContain("agentStatus !== 'idle'")
    expect(effect).not.toContain('deliveredJobIds')
  })

  it('marks the first visible event of each run as a new assistant turn', async () => {
    const { readFileSync } = await import('node:fs')
    const { resolve } = await import('node:path')
    const source = readFileSync(
      resolve(process.cwd(), 'src/hooks/useStreamEvents.ts'),
      'utf8',
    )

    expect(source).toContain('pendingAssistantTurnBoundaryRef.current = true')
    expect(source).toContain('consumeAssistantTurnBoundary()')
    expect(source).toContain('startsNewAssistantTurn: true')
  })
})
