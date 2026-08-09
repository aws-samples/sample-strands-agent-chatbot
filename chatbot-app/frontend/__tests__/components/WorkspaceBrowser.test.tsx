import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WorkspaceBrowser } from '@/components/canvas/WorkspaceBrowser'
import { apiFetch } from '@/lib/api-client'

vi.mock('@/lib/api-client', () => ({
  apiFetch: vi.fn(),
}))

vi.mock('@/components/ui/Markdown', () => ({
  Markdown: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="markdown-preview">{children}</div>
  ),
}))

vi.mock('@/components/canvas/OfficeViewer', () => ({
  OfficeViewer: ({ filename }: { filename: string }) => (
    <div data-testid="office-preview">{filename}</div>
  ),
}))

const response = (body: unknown, ok = true) => ({
  ok,
  json: vi.fn().mockResolvedValue(body),
})

describe('WorkspaceBrowser', () => {
  const mockApiFetch = vi.mocked(apiFetch)

  beforeEach(() => {
    vi.clearAllMocks()
    mockApiFetch.mockImplementation(async endpoint => {
      if (endpoint === 'workspace/entries') {
        return response({
          entries: [{
            id: 'documents',
            path: 'documents',
            parentPath: '',
            name: 'Documents',
            kind: 'directory',
          }],
        }) as any
      }
      if (endpoint === 'workspace/entries?path=documents') {
        return response({
          entries: [{
            id: 'report',
            path: 'documents/report.md',
            parentPath: 'documents',
            name: 'report.md',
            kind: 'file',
            size: 1200,
            previewKind: 'markdown',
          }],
        }) as any
      }
      if (endpoint === 'workspace/preview?path=documents%2Freport.md') {
        return response({
          entry: {
            id: 'report',
            path: 'documents/report.md',
            parentPath: 'documents',
            name: 'report.md',
            kind: 'file',
          },
          kind: 'markdown',
          content: '# Session report',
        }) as any
      }
      throw new Error(`Unexpected endpoint: ${endpoint}`)
    })
  })

  it('lazy-loads a directory and previews a selected file', async () => {
    render(<WorkspaceBrowser sessionId="session-1" />)

    const documents = await screen.findByRole('button', { name: /documents/i })
    fireEvent.click(documents)

    const report = await screen.findByRole('button', { name: /report\.md/i })
    expect(report).toHaveTextContent('1.2 KB')
    fireEvent.click(report)

    expect(await screen.findByTestId('markdown-preview')).toHaveTextContent('# Session report')
    expect(screen.getByText('documents/report.md')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /back to workspace files/i }))
    expect(await screen.findByRole('button', { name: /documents/i })).toBeInTheDocument()
  })

  it('scopes every request to the active session', async () => {
    render(<WorkspaceBrowser sessionId="session-1" />)
    await screen.findByRole('button', { name: /documents/i })

    expect(mockApiFetch).toHaveBeenCalledWith(
      'workspace/entries',
      { headers: { 'X-Session-ID': 'session-1' } },
    )
  })

  it('shows an explicit empty-session state', () => {
    render(<WorkspaceBrowser />)
    expect(screen.getByText(/start a conversation/i)).toBeInTheDocument()
    expect(mockApiFetch).not.toHaveBeenCalled()
  })

  it('surfaces list failures without discarding the browser', async () => {
    mockApiFetch.mockResolvedValueOnce(response({}, false) as any)
    render(<WorkspaceBrowser sessionId="session-1" />)

    await waitFor(() => {
      expect(screen.getByText('Unable to load workspace')).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /refresh workspace files/i })).toBeInTheDocument()
  })
})
