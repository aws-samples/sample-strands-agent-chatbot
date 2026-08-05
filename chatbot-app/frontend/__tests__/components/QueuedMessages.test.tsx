import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueuedMessages } from '@/components/chat/QueuedMessages'
import type { QueuedMessage } from '@/hooks/useMessageQueue'

function msg(text: string, files: File[] = []): QueuedMessage {
  return { id: `id-${text}`, text, files, sessionId: 's1' }
}

function setup(queue: QueuedMessage[], holdReason: any = null) {
  const handlers = {
    onRemove: vi.fn(),
    onSendNow: vi.fn(),
    onDiscardAll: vi.fn(),
  }
  render(<QueuedMessages queue={queue} holdReason={holdReason} {...handlers} />)
  return handlers
}

const removeButtons = () =>
  screen.getAllByRole('button', { name: /remove queued message/i })

describe('QueuedMessages', () => {
  it('renders nothing when the queue is empty', () => {
    const { container } = render(
      <QueuedMessages
        queue={[]}
        holdReason={null}
        onRemove={vi.fn()}
        onSendNow={vi.fn()}
        onDiscardAll={vi.fn()}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows one remove button per queued message', () => {
    setup([msg('first'), msg('second')])
    expect(removeButtons()).toHaveLength(2)
  })

  // A hover-only control cannot be reached on touch devices and reads as
  // "there is no way to cancel this" elsewhere, which is what prompted this.
  // Asserts on the button's own classes rather than toBeVisible(): the chip's
  // entrance animation leaves inline opacity:0 on the wrapper under jsdom, which
  // would make the check fail for a reason unrelated to hover.
  it('keeps every remove button visible without hovering', () => {
    setup([msg('first'), msg('second')])
    for (const button of removeButtons()) {
      expect(button.className).not.toMatch(/opacity-0/)
      expect(button.className).not.toMatch(/group-hover:/)
      expect(button).not.toBeDisabled()
    }
  })

  it('removes the message that was clicked', () => {
    const { onRemove } = setup([msg('first'), msg('second')])

    fireEvent.click(removeButtons()[1])

    expect(onRemove).toHaveBeenCalledTimes(1)
    expect(onRemove).toHaveBeenCalledWith('id-second')
  })

  it('labels each remove button with its message so they are distinguishable', () => {
    setup([msg('write the tests'), msg('update the readme')])
    expect(screen.getByRole('button', { name: /write the tests/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /update the readme/i })).toBeInTheDocument()
  })

  it('describes an attachment-only message instead of showing empty text', () => {
    setup([msg('', [new File(['x'], 'a.txt'), new File(['y'], 'b.txt')])])
    expect(screen.getByText('2 attachment(s)')).toBeInTheDocument()
  })

  it('hides the confirmation bar until the queue is held', () => {
    setup([msg('queued')])
    expect(screen.queryByRole('button', { name: /^send$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /discard/i })).not.toBeInTheDocument()
  })

  it('offers send and discard once held, and says why', () => {
    const { onSendNow, onDiscardAll } = setup([msg('queued')], 'stopped')

    expect(screen.getByText(/you stopped the last turn/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /^send$/i }))
    expect(onSendNow).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: /discard/i }))
    expect(onDiscardAll).toHaveBeenCalledTimes(1)
  })

  // "Discard" alone does not say whether it drops one chip or the whole list.
  it('states how many messages discard would drop', () => {
    setup([msg('a'), msg('b'), msg('c')], 'error')
    expect(screen.getByRole('button', { name: /discard all 3/i })).toBeInTheDocument()
  })

  it('explains an approval hold differently from an error hold', () => {
    setup([msg('a')], 'interrupt')
    expect(screen.getByText(/waiting for your approval/i)).toBeInTheDocument()
  })
})
