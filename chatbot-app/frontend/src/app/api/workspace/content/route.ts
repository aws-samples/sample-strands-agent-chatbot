import { NextRequest, NextResponse } from 'next/server'
import { Readable } from 'node:stream'
import { extractUserFromRequest } from '@/lib/auth-utils'
import {
  openMountedWorkspaceFile,
  WorkspacePathError,
} from '@/lib/workspace/s3-repository'

export const runtime = 'nodejs'

export async function GET(request: NextRequest) {
  try {
    const sessionId = request.nextUrl.searchParams.get('sessionId')
    const path = request.nextUrl.searchParams.get('path')
    if (!sessionId || !path) {
      return NextResponse.json({ error: 'Missing sessionId or path' }, { status: 400 })
    }

    const user = await extractUserFromRequest(request)
    const file = await openMountedWorkspaceFile(user.userId, sessionId, path)
    if (!file) {
      return NextResponse.json({ error: 'Mounted workspace unavailable' }, { status: 404 })
    }

    const metadata = file.metadata
    const rangeHeader = request.headers.get('range')
    let start = 0
    let end = metadata.size - 1
    let status = 200

    if (rangeHeader) {
      const match = /^bytes=(\d+)-(\d*)$/.exec(rangeHeader)
      if (!match) {
        await file.handle.close()
        return new NextResponse(null, {
          status: 416,
          headers: { 'Content-Range': `bytes */${metadata.size}` },
        })
      }
      start = Number(match[1])
      end = match[2] ? Number(match[2]) : end
      if (start > end || end >= metadata.size) {
        await file.handle.close()
        return new NextResponse(null, {
          status: 416,
          headers: { 'Content-Range': `bytes */${metadata.size}` },
        })
      }
      status = 206
    }

    const stream = file.handle.createReadStream({ start, end, autoClose: true })
    const headers = new Headers({
      'Accept-Ranges': 'bytes',
      'Cache-Control': 'private, no-store',
      'Content-Length': String(end - start + 1),
      'Content-Type': file.mimeType,
    })
    if (status === 206) {
      headers.set('Content-Range', `bytes ${start}-${end}/${metadata.size}`)
    }
    if (request.nextUrl.searchParams.get('download') === '1') {
      headers.set(
        'Content-Disposition',
        `attachment; filename="${file.name.replace(/"/g, '')}"`,
      )
    }

    return new NextResponse(Readable.toWeb(stream) as ReadableStream, {
      status,
      headers,
    })
  } catch (error) {
    if (error instanceof WorkspacePathError) {
      return NextResponse.json({ error: error.message }, { status: 400 })
    }
    console.error('[WorkspaceContent] Error:', error)
    return NextResponse.json({ error: 'Failed to read workspace file' }, { status: 500 })
  }
}
