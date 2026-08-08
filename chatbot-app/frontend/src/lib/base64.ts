const BASE64_CHUNK_SIZE = 0x8000

export function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  const chunks: string[] = []

  for (let offset = 0; offset < bytes.length; offset += BASE64_CHUNK_SIZE) {
    chunks.push(String.fromCharCode(...bytes.subarray(
      offset,
      Math.min(offset + BASE64_CHUNK_SIZE, bytes.length),
    )))
  }

  return btoa(chunks.join(''))
}
