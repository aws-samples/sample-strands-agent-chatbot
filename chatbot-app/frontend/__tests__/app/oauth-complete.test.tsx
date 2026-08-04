import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"

import OAuthCompletePage from "@/app/oauth-complete/page"

vi.mock("aws-amplify/auth", () => ({
  fetchAuthSession: vi.fn(async () => ({
    tokens: { accessToken: { toString: () => "access-token" } },
  })),
}))

const SESSION_URI = "urn:ietf:params:oauth:request_uri:abc123"

function renderWithQuery(query: string) {
  window.history.replaceState({}, "", `/oauth-complete?${query}`)
  return render(<OAuthCompletePage />)
}

describe("OAuthCompletePage", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.restoreAllMocks()
    fetchMock = vi.fn(async () => ({ ok: true, status: 200 }) as unknown as Response)
    vi.stubGlobal("fetch", fetchMock)
    vi.spyOn(window, "close").mockImplementation(() => {})
  })

  // AWS documents that customState is echoed to the callback URL but never
  // names the query parameter, so every plausible spelling must work.
  it.each(["state", "customState", "custom_state"])(
    "completes the flow when the elicitation ID arrives as %s",
    async (param) => {
      renderWithQuery(
        `session_id=${encodeURIComponent(SESSION_URI)}&${param}=elicitation-123`
      )

      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
      const [url, init] = fetchMock.mock.calls[0]
      expect(url).toBe("/api/stream/elicitation-complete")
      expect(JSON.parse((init as RequestInit).body as string)).toEqual({
        elicitationId: "elicitation-123",
        oauthSessionUri: SESSION_URI,
      })
    }
  )

  it("fails without calling the BFF when no state parameter is present", async () => {
    renderWithQuery(`session_id=${encodeURIComponent(SESSION_URI)}`)

    expect(await screen.findByText("Authorization Failed")).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("fails without calling the BFF when session_id is missing", async () => {
    renderWithQuery("state=elicitation-123")

    expect(await screen.findByText("Authorization Failed")).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
