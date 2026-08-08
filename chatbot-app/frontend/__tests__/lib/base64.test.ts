import { describe, expect, it } from 'vitest'

import { arrayBufferToBase64 } from '@/lib/base64'

describe('arrayBufferToBase64', () => {
  it('encodes binary data across chunk boundaries', () => {
    const bytes = new Uint8Array(70_000)
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = index % 256
    }

    expect(arrayBufferToBase64(bytes.buffer)).toBe(
      Buffer.from(bytes).toString('base64'),
    )
  })
})
