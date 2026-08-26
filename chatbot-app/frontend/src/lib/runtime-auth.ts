import { fetchAuthSession } from 'aws-amplify/auth'
import { isRuntimeTokenFresh } from '@/lib/runtime-auth-policy'

type AuthSession = Awaited<ReturnType<typeof fetchAuthSession>>

let refreshInFlight: Promise<AuthSession> | null = null

async function refreshAuthSession(): Promise<AuthSession> {
  if (!refreshInFlight) {
    refreshInFlight = fetchAuthSession({ forceRefresh: true }).finally(() => {
      refreshInFlight = null
    })
  }
  return refreshInFlight
}

function accessTokenFrom(session: AuthSession) {
  return session.tokens?.accessToken
}

export async function getRuntimeAccessToken(
  options: { forceRefresh?: boolean } = {},
): Promise<string | null> {
  let session = options.forceRefresh
    ? await refreshAuthSession()
    : await fetchAuthSession()
  let accessToken = accessTokenFrom(session)

  if (!accessToken) return null

  if (
    !options.forceRefresh
    && !isRuntimeTokenFresh(accessToken.payload.exp)
  ) {
    session = await refreshAuthSession()
    accessToken = accessTokenFrom(session)
  }

  if (!accessToken) return null
  if (!isRuntimeTokenFresh(accessToken.payload.exp)) {
    throw new Error('Refreshed authentication token is not valid long enough for AgentCore Runtime')
  }

  return accessToken.toString()
}

export async function getRuntimeAuthHeaders(
  options: { forceRefresh?: boolean } = {},
): Promise<Record<string, string>> {
  const token = await getRuntimeAccessToken(options)
  return token ? { Authorization: `Bearer ${token}` } : {}
}
