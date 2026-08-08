import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  Sidebar,
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from '@/components/ui/sidebar'

describe('Sidebar off-canvas positioning', () => {
  it('moves the full sidebar box off-screen when collapsed', () => {
    render(
      <SidebarProvider defaultOpen={false}>
        <Sidebar>
          <div>Sidebar content</div>
        </Sidebar>
        <SidebarInset>
          <SidebarTrigger />
        </SidebarInset>
      </SidebarProvider>,
    )

    const panel = screen.getByText('Sidebar content').parentElement?.parentElement
    expect(panel).toHaveClass('left-0')
    expect(panel).toHaveClass('-translate-x-full')

    fireEvent.click(screen.getByRole('button', { name: 'Toggle Sidebar' }))

    expect(panel).toHaveClass('translate-x-0')
    expect(panel).not.toHaveClass('-translate-x-full')
  })
})
