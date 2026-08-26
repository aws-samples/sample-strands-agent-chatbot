export const RUNTIME_TOKEN_MIN_VALIDITY_MS = 2 * 60 * 1000

export function isRuntimeTokenFresh(
  expiresAtSeconds: number | undefined,
  nowMs: number = Date.now(),
): boolean {
  return (
    typeof expiresAtSeconds === 'number'
    && Number.isFinite(expiresAtSeconds)
    && expiresAtSeconds * 1000 - nowMs >= RUNTIME_TOKEN_MIN_VALIDITY_MS
  )
}
