import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"

import { OAuthElicitationDialog } from "@/components/OAuthElicitationDialog"

vi.mock("lucide-react", () => ({
  ExternalLink: () => <span data-testid="external-link" />,
  KeyRound: () => <span data-testid="key-round" />,
  X: () => <span data-testid="close-icon" />,
}))

describe("OAuthElicitationDialog", () => {
  const oauth = {
    authUrl: "https://example.com/oauth",
    serviceName: "Gmail",
    popupOpened: false,
    elicitationId: "elicitation-123",
  }

  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it("shows a user-initiated authorization action", () => {
    render(
      <OAuthElicitationDialog
        oauth={oauth}
        sessionId="session-123"
        onCancel={vi.fn()}
      />
    )

    expect(screen.getByText("Connect Gmail")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument()
  })

  it("opens the authorization URL and persists callback context", () => {
    const focus = vi.fn()
    const open = vi.spyOn(window, "open").mockReturnValue({ focus } as unknown as Window)

    render(
      <OAuthElicitationDialog
        oauth={oauth}
        sessionId="session-123"
        onCancel={vi.fn()}
      />
    )
    fireEvent.click(screen.getByRole("button", { name: "Continue" }))

    expect(open).toHaveBeenCalledWith(
      oauth.authUrl,
      "oauth_popup",
      "width=500,height=700,scrollbars=yes,resizable=yes"
    )
    expect(focus).toHaveBeenCalled()
    expect(localStorage.setItem).toHaveBeenCalledWith(
      "oauth_pending",
      JSON.stringify({
        sessionId: "session-123",
        elicitationId: "elicitation-123",
      })
    )
  })

  it("shows recovery guidance when the popup is blocked", () => {
    vi.spyOn(window, "open").mockReturnValue(null)

    render(
      <OAuthElicitationDialog
        oauth={oauth}
        sessionId="session-123"
        onCancel={vi.fn()}
      />
    )
    fireEvent.click(screen.getByRole("button", { name: "Continue" }))

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Allow popups for this site"
    )
  })

  it("cancels the pending authorization", () => {
    const onCancel = vi.fn()
    render(
      <OAuthElicitationDialog
        oauth={oauth}
        sessionId="session-123"
        onCancel={onCancel}
      />
    )

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
    expect(onCancel).toHaveBeenCalledOnce()
  })
})
