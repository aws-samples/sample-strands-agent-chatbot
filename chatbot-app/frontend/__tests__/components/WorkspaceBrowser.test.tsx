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
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }))
    mockApiFetch.mockImplementation(async endpoint => {
      if (endpoint === 'workspace/entries') {
        return response({
          entries: [
            {
              id: 'uploads',
              path: 'uploads',
              parentPath: '',
              name: 'Uploads',
              kind: 'directory',
            },
            {
              id: 'documents',
              path: 'documents',
              parentPath: '',
              name: 'Documents',
              kind: 'directory',
            },
          ],
        }) as any
      }
      if (endpoint === 'workspace/entries?path=documents') {
        return response({
          entries: [
            {
              id: 'report',
              path: 'documents/report.md',
              parentPath: 'documents',
              name: 'report.md',
              kind: 'file',
              size: 1200,
              previewKind: 'markdown',
            },
            {
              id: 'data',
              path: 'documents/data.json',
              parentPath: 'documents',
              name: 'data.json',
              kind: 'file',
              size: 24,
              previewKind: 'json',
            },
          ],
        }) as any
      }
      if (endpoint === 'workspace/entries?path=uploads') {
        return response({
          entries: [{
            id: 'uploaded-data',
            path: 'uploads/uploaded.jsonl',
            parentPath: 'uploads',
            name: 'uploaded.jsonl',
            kind: 'file',
            size: 9,
            previewKind: 'json',
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
      if (endpoint === 'workspace/preview?path=documents%2Fdata.json') {
        return response({
          entry: {
            id: 'data',
            path: 'documents/data.json',
            parentPath: 'documents',
            name: 'data.json',
            kind: 'file',
          },
          kind: 'json',
          content: '{"name":"workspace","enabled":true}',
        }) as any
      }
      if (endpoint === 'workspace/preview?path=uploads%2Fuploaded.jsonl') {
        return response({
          entry: {
            id: 'uploaded-data',
            path: 'uploads/uploaded.jsonl',
            parentPath: 'uploads',
            name: 'uploaded.jsonl',
            kind: 'file',
          },
          kind: 'json',
          content: '{"id":1,"active":true}\nnot-json\n{"id":2}',
        }) as any
      }
      if (endpoint === 'workspace/upload') {
        return response({
          uploadUrl: 'https://uploads.example.test/presigned',
          entry: {
            id: 'uploaded-data',
            path: 'uploads/uploaded.jsonl',
            parentPath: 'uploads',
            name: 'uploaded.jsonl',
            kind: 'file',
          },
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

  it('pretty-prints JSON files in the preview', async () => {
    render(<WorkspaceBrowser sessionId="session-1" />)
    fireEvent.click(await screen.findByRole('button', { name: /documents/i }))
    fireEvent.click(await screen.findByRole('button', { name: /data\.json/i }))

    const preview = await screen.findByTestId('json-preview')
    expect(preview).toHaveTextContent('"name": "workspace"')
    expect(preview).toHaveTextContent('"enabled": true')
  })

  it('pretty-prints valid JSONL records without hiding invalid lines', async () => {
    render(<WorkspaceBrowser sessionId="session-1" />)
    fireEvent.click(await screen.findByRole('button', { name: /uploads/i }))
    fireEvent.click(await screen.findByRole('button', { name: /uploaded\.jsonl/i }))

    const preview = await screen.findByTestId('json-preview')
    expect(preview).toHaveTextContent('"id": 1')
    expect(preview).toHaveTextContent('"active": true')
    expect(preview).toHaveTextContent('not-json')
    expect(preview).toHaveTextContent('"id": 2')
  })

  it('uploads files into the session workspace and opens Uploads', async () => {
    render(<WorkspaceBrowser sessionId="session-1" />)
    await screen.findByRole('button', { name: /uploads/i })

    const input = screen.getByLabelText('Choose workspace files')
    const file = new File(['{"id":1}\n'], 'uploaded.jsonl', {
      type: 'application/x-ndjson',
    })
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => {
      expect(mockApiFetch).toHaveBeenCalledWith(
        'workspace/upload',
        expect.objectContaining({
          method: 'POST',
          headers: { 'X-Session-ID': 'session-1' },
          body: JSON.stringify({
            name: 'uploaded.jsonl',
            mimeType: 'application/x-ndjson',
            size: 9,
          }),
        }),
      )
    })
    expect(fetch).toHaveBeenCalledWith(
      'https://uploads.example.test/presigned',
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/x-ndjson' },
        body: file,
      },
    )
    expect(await screen.findByRole('button', { name: /uploaded\.jsonl/i }))
      .toBeInTheDocument()
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
