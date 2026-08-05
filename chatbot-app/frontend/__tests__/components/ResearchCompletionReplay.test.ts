/**
 * A past research must not be "completed" again after a page reload.
 *
 * researchData is derived from message history, so every research a session has
 * ever run reappears as status 'complete' on each mount. The completion handler
 * dedupes with a useRef Set, which starts empty on every mount — so after a
 * reload the first render replays old completions. Each replay resets the
 * research state and opens that research's artifact, which tears down the
 * approval card for the research the user is starting right now.
 *
 * That is why the bug only showed up with artifacts already present, and only
 * after a reload: without a reload the Set still holds the earlier ids.
 *
 * Canvas's own priority rule is covered in CanvasResearchApproval.test.tsx.
 * This covers the guard that stops the state from being torn down before Canvas
 * ever gets the chance to apply it.
 */
import { describe, it, expect, vi } from 'vitest'

interface ResearchExecution {
  status: string
  result: string
}

/**
 * The completion loop from ChatInterface, with the surrounding component state
 * passed in. Kept as a function of its inputs so the ordering that produced the
 * bug (interrupt arrives, then history replays) can be driven directly.
 */
function runCompletionPass(
  researchData: Map<string, ResearchExecution>,
  artifacts: Array<{ id: string }>,
  processed: Set<string>,
  researchArtifactId: string | null,
  handlers: {
    onComplete: (executionId: string) => void
    onReset: () => void
    onOpenArtifact: (id: string) => void
  },
) {
  if (!researchArtifactId) return

  for (const [executionId, data] of researchData) {
    if (processed.has(executionId)) continue

    // The guard: an artifact for this research already exists, so it finished
    // before this mount and must not be completed again.
    if (artifacts.some(a => a.id === `research-${executionId}`)) {
      processed.add(executionId)
      continue
    }

    if (data.status === 'complete' && data.result) {
      processed.add(executionId)
      handlers.onComplete(executionId)
      handlers.onReset()
      handlers.onOpenArtifact(`research-${executionId}`)
    }
  }
}

/** Reads the shipped component source; vitest runs with the frontend as cwd. */
async function readChatInterfaceSource(): Promise<string> {
  const { readFileSync } = await import('node:fs')
  const { resolve } = await import('node:path')
  return readFileSync(resolve(process.cwd(), 'src/components/ChatInterface.tsx'), 'utf8')
}

function setup() {
  return {
    onComplete: vi.fn(),
    onReset: vi.fn(),
    onOpenArtifact: vi.fn(),
  }
}

describe('research completion replay after reload', () => {
  it('does not re-complete a research that already has an artifact', () => {
    const handlers = setup()
    const researchData = new Map([
      ['exec-old', { status: 'complete', result: '<research># Old</research>' }],
    ])
    const artifacts = [{ id: 'research-exec-old' }]

    // Fresh mount: the dedupe set is empty, as it is after every reload.
    runCompletionPass(researchData, artifacts, new Set(), 'in-progress', handlers)

    expect(handlers.onReset).not.toHaveBeenCalled()
    expect(handlers.onOpenArtifact).not.toHaveBeenCalled()
  })

  // The user-visible failure: the approval card is replaced by the old report.
  it('leaves a pending approval intact when an old research replays', () => {
    const handlers = setup()
    const researchData = new Map([
      ['exec-old', { status: 'complete', result: '<research># Old</research>' }],
    ])
    const artifacts = [{ id: 'research-exec-old' }]

    // 'in-progress' is what the interrupt handler sets while awaiting approval.
    runCompletionPass(researchData, artifacts, new Set(), 'in-progress', handlers)

    expect(handlers.onReset).not.toHaveBeenCalled()
  })

  it('still completes a research that finished during this mount', () => {
    const handlers = setup()
    const researchData = new Map([
      ['exec-new', { status: 'complete', result: '<research># New</research>' }],
    ])

    // No artifact yet — the backend saves it, but this pass is what adds it locally.
    runCompletionPass(researchData, [], new Set(), 'in-progress', handlers)

    expect(handlers.onComplete).toHaveBeenCalledWith('exec-new')
    expect(handlers.onOpenArtifact).toHaveBeenCalledWith('research-exec-new')
  })

  it('completes only the new research when an old one is also present', () => {
    const handlers = setup()
    const researchData = new Map([
      ['exec-old', { status: 'complete', result: '<research># Old</research>' }],
      ['exec-new', { status: 'complete', result: '<research># New</research>' }],
    ])
    const artifacts = [{ id: 'research-exec-old' }]

    runCompletionPass(researchData, artifacts, new Set(), 'in-progress', handlers)

    expect(handlers.onComplete).toHaveBeenCalledTimes(1)
    expect(handlers.onComplete).toHaveBeenCalledWith('exec-new')
    expect(handlers.onOpenArtifact).toHaveBeenCalledWith('research-exec-new')
  })

  it('marks the replayed research processed so later passes skip it', () => {
    const handlers = setup()
    const researchData = new Map([
      ['exec-old', { status: 'complete', result: '<research># Old</research>' }],
    ])
    const artifacts = [{ id: 'research-exec-old' }]
    const processed = new Set<string>()

    runCompletionPass(researchData, artifacts, processed, 'in-progress', handlers)

    expect(processed.has('exec-old')).toBe(true)
  })

  // runCompletionPass above mirrors ChatInterface rather than importing it (the
  // logic lives inside a useEffect in a component with ~40 hooks). That mirror
  // can drift, so assert the guard is actually present in the shipped source.
  it('the shipped completion effect guards on an existing artifact', async () => {
    const source = await readChatInterfaceSource()

    const effect = source.slice(source.indexOf('Clean up when research completes'))
    const body = effect.slice(0, effect.indexOf('}, ['))

    expect(body).toMatch(/artifacts\.some\(\s*a\s*=>\s*a\.id === `research-\$\{executionId\}`\s*\)/)
    expect(body).toContain('processedResearchIdsRef.current.add(executionId)')
  })

  it('the shipped effect re-runs when artifacts change', async () => {
    const source = await readChatInterfaceSource()

    const effect = source.slice(source.indexOf('Clean up when research completes'))
    const deps = effect.slice(effect.indexOf('}, ['), effect.indexOf('])', effect.indexOf('}, [')))

    // Without artifacts in the dependency list the guard reads a stale list and
    // the replay slips through on the render where it matters.
    expect(deps).toContain('artifacts')
  })

  it('does nothing when no research is active', () => {
    const handlers = setup()
    const researchData = new Map([
      ['exec-new', { status: 'complete', result: '<research># New</research>' }],
    ])

    runCompletionPass(researchData, [], new Set(), null, handlers)

    expect(handlers.onComplete).not.toHaveBeenCalled()
  })
})
