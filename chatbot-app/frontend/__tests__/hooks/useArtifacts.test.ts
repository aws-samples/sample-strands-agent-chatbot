import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useArtifacts } from '@/hooks/useArtifacts'
import type { Artifact } from '@/types/artifact'

const firstArtifact: Artifact = {
  id: 'research-1',
  type: 'research',
  title: 'First session report',
  content: '# Report',
  timestamp: '2026-08-06T00:00:00Z',
}

describe('useArtifacts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('does not expose or copy artifacts when the active session changes', async () => {
    const hook = renderHook(
      ({ sessionId }) => useArtifacts(sessionId),
      { initialProps: { sessionId: 'session-1' } },
    )

    await waitFor(() => {
      expect(hook.result.current.artifacts).toEqual([])
    })

    act(() => {
      hook.result.current.addArtifact(firstArtifact)
    })
    expect(hook.result.current.artifacts).toEqual([firstArtifact])

    const staleAddArtifact = hook.result.current.addArtifact
    hook.rerender({ sessionId: 'session-2' })

    expect(hook.result.current.artifacts).toEqual([])
    expect(hook.result.current.selectedArtifactId).toBeNull()

    act(() => {
      staleAddArtifact({
        ...firstArtifact,
        id: 'late-session-1-artifact',
      })
    })

    expect(hook.result.current.artifacts).toEqual([])

    const secondArtifact: Artifact = {
      ...firstArtifact,
      id: 'session-2-artifact',
      title: 'Second session report',
    }
    act(() => {
      hook.result.current.addArtifact(secondArtifact)
    })

    expect(hook.result.current.artifacts).toEqual([secondArtifact])
    expect(sessionStorage.setItem).toHaveBeenLastCalledWith(
      'artifacts-session-2',
      JSON.stringify([secondArtifact]),
    )
  })
})
