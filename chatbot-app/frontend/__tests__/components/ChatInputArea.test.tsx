import React, { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ChatInputArea,
  LARGE_PASTE_ATTACHMENT_THRESHOLD,
  MAX_PASTED_TEXT_BYTES,
  MAX_STRUCTURED_CHAT_FILE_BYTES,
} from '@/components/chat/ChatInputArea'
import { apiFetch } from '@/lib/api-client'
import type { WorkspaceAttachment } from '@/types/chat'

vi.mock('@/lib/api-client', () => ({
  apiFetch: vi.fn(),
}))

vi.mock('@/components/ModelConfigDialog', () => ({
  ModelConfigDialog: () => null,
}))

function Harness({
  onSendMessage = vi.fn().mockResolvedValue(undefined),
}: {
  onSendMessage?: (
    text: string,
    files: File[],
    workspaceFiles: WorkspaceAttachment[],
  ) => Promise<void>
}) {
  const [files, setFiles] = useState<File[]>([])

  return (
    <>
      <ChatInputArea
        selectedFiles={files}
        setSelectedFiles={setFiles}
        agentStatus="idle"
        isBusy={false}
        isVoiceActive={false}
        isVoiceSupported={false}
        isCanvasOpen={false}
        sessionId="session-1"
        onSendMessage={onSendMessage}
        onEnqueueMessage={vi.fn()}
        conciseMode={false}
        onToggleConciseMode={vi.fn()}
        onStopGeneration={vi.fn()}
        onConnectVoice={vi.fn().mockResolvedValue(undefined)}
        onDisconnectVoice={vi.fn()}
        onExportConversation={vi.fn()}
        onNewChat={vi.fn().mockResolvedValue(undefined)}
        onCompact={vi.fn()}
      />
      <output data-testid="attachment-count">{files.length}</output>
      <output data-testid="attachment-name">{files[0]?.name}</output>
      <output data-testid="attachment-size">{files[0]?.size}</output>
    </>
  )
}

describe('ChatInputArea large paste handling', () => {
  it('converts a large text paste into a text attachment', () => {
    render(<Harness />)
    const pastedText = 'a'.repeat(LARGE_PASTE_ATTACHMENT_THRESHOLD)

    fireEvent.paste(screen.getByRole('textbox'), {
      clipboardData: {
        items: [],
        getData: (type: string) => type === 'text/plain' ? pastedText : '',
      },
    })

    expect(screen.getByTestId('attachment-count')).toHaveTextContent('1')
    expect(screen.getByTestId('attachment-name')).toHaveTextContent(
      /^pasted-text-.*\.txt$/,
    )
    expect(screen.getByTestId('attachment-size')).toHaveTextContent(
      String(pastedText.length),
    )
  })

  it('keeps ordinary text pastes in the textarea path', () => {
    render(<Harness />)

    fireEvent.paste(screen.getByRole('textbox'), {
      clipboardData: {
        items: [],
        getData: () => 'short paste',
      },
    })

    expect(screen.getByTestId('attachment-count')).toHaveTextContent('0')
  })

  it('rejects pasted text that would exceed the attachment payload limit', () => {
    render(<Harness />)
    const pastedText = 'a'.repeat(MAX_PASTED_TEXT_BYTES + 1)

    fireEvent.paste(screen.getByRole('textbox'), {
      clipboardData: {
        items: [],
        getData: () => pastedText,
      },
    })

    expect(screen.getByTestId('attachment-count')).toHaveTextContent('0')
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Pasted text exceeds the 1 MB limit',
    )
  })
})

describe('ChatInputArea structured data attachments', () => {
  const mockApiFetch = vi.mocked(apiFetch)

  beforeEach(() => {
    vi.clearAllMocks()
    mockApiFetch.mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        entry: {
          name: 'large.json',
          path: 'uploads/large.json',
          mimeType: 'application/json',
          size: MAX_STRUCTURED_CHAT_FILE_BYTES + 1,
        },
        uploadUrl: 'https://uploads.example.test/presigned',
      }),
    } as any)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }))
  })

  it('accepts JSONL files within the chat attachment limit', () => {
    render(<Harness />)
    const input = document.getElementById('file-upload') as HTMLInputElement
    const file = new File(['{"id":1}\n'], 'records.jsonl', {
      type: 'application/x-ndjson',
    })

    fireEvent.change(input, { target: { files: [file] } })

    expect(screen.getByTestId('attachment-count')).toHaveTextContent('1')
    expect(screen.getByTestId('attachment-name')).toHaveTextContent('records.jsonl')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('uploads oversized JSON files to Workspace and sends their path', async () => {
    const onSendMessage = vi.fn().mockResolvedValue(undefined)
    render(<Harness onSendMessage={onSendMessage} />)
    const input = document.getElementById('file-upload') as HTMLInputElement
    const file = new File(
      [new Uint8Array(MAX_STRUCTURED_CHAT_FILE_BYTES + 1)],
      'large.json',
      { type: 'application/json' },
    )

    fireEvent.change(input, { target: { files: [file] } })

    expect(screen.getByTestId('attachment-count')).toHaveTextContent('0')
    expect(await screen.findByText('Workspace')).toBeInTheDocument()
    expect(screen.getByText('large.json')).toBeInTheDocument()
    expect(mockApiFetch).toHaveBeenCalledWith(
      'workspace/upload',
      expect.objectContaining({
        method: 'POST',
        headers: { 'X-Session-ID': 'session-1' },
      }),
    )
    expect(fetch).toHaveBeenCalledWith(
      'https://uploads.example.test/presigned',
      expect.objectContaining({
        method: 'PUT',
        body: file,
      }),
    )

    fireEvent.click(screen.getByTitle('Send'))

    await waitFor(() => {
      expect(onSendMessage).toHaveBeenCalledWith('', [], [{
        name: 'large.json',
        type: 'application/json',
        size: MAX_STRUCTURED_CHAT_FILE_BYTES + 1,
        path: 'uploads/large.json',
      }])
    })
  })

  it('keeps a failed Workspace upload in the composer as an error', async () => {
    mockApiFetch.mockResolvedValue({
      ok: false,
      json: vi.fn().mockResolvedValue({ error: 'File exceeds the 100 MB workspace upload limit' }),
    } as any)
    render(<Harness />)
    const input = document.getElementById('file-upload') as HTMLInputElement
    const file = new File(
      [new Uint8Array(MAX_STRUCTURED_CHAT_FILE_BYTES + 1)],
      'large.json',
      { type: 'application/json' },
    )

    fireEvent.change(input, { target: { files: [file] } })

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'File exceeds the 100 MB workspace upload limit',
    )
    expect(screen.queryByText('Workspace')).not.toBeInTheDocument()
  })
})
