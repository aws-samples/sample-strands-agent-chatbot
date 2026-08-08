import React, { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import {
  ChatInputArea,
  LARGE_PASTE_ATTACHMENT_THRESHOLD,
  MAX_PASTED_TEXT_BYTES,
} from '@/components/chat/ChatInputArea'

vi.mock('@/components/ModelConfigDialog', () => ({
  ModelConfigDialog: () => null,
}))

function Harness() {
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
        onSendMessage={vi.fn().mockResolvedValue(undefined)}
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
