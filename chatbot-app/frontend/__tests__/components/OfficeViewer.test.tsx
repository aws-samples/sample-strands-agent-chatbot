import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { OfficeViewer } from '@/components/canvas/OfficeViewer'

describe('OfficeViewer', () => {
  it('uses a storage-neutral signed preview URL without another S3 lookup', async () => {
    render(
      <OfficeViewer
        previewUrl="https://example.test/report.docx?signature=short-lived"
        filename="report.docx"
      />,
    )

    const frame = await screen.findByTitle('Preview: report.docx')
    expect(frame).toHaveAttribute(
      'src',
      expect.stringContaining('https%3A%2F%2Fexample.test%2Freport.docx'),
    )
    expect(fetch).not.toHaveBeenCalled()
  })

  it('reports an invalid legacy S3 URL instead of rendering a broken frame', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    render(<OfficeViewer s3Url="not-an-s3-url" filename="report.docx" />)

    await waitFor(() => {
      expect(screen.getByText(/invalid s3 url format/i)).toBeInTheDocument()
    })
    expect(screen.queryByTitle('Preview: report.docx')).not.toBeInTheDocument()
    consoleError.mockRestore()
  })
})
