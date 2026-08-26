import { NextRequest, NextResponse } from 'next/server'
import { SSMClient, GetParameterCommand } from '@aws-sdk/client-ssm'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { codeInterpreterWorkspaceId } from '@/lib/workspace/s3-repository'

const region = process.env.AWS_REGION || 'us-west-2'

/**
 * POST /api/documents/download
 *
 * Reconstructs S3 key from userId, sessionId and filename, returns it for frontend to fetch presigned URL.
 * userId is extracted from the Authorization header (JWT token).
 *
 * Request body:
 * - sessionId: string (chat session ID)
 * - filename: string (document filename)
 * - toolType: string (e.g., 'word_document')
 *
 * Returns:
 * - s3Key: string (s3://bucket/path format for presigned URL generation)
 */
export async function POST(request: NextRequest) {
  try {
    const { sessionId, filename, toolType } = await request.json()

    if (!sessionId || !filename || !toolType) {
      return NextResponse.json(
        { error: 'Missing required fields: sessionId, filename, toolType' },
        { status: 400 }
      )
    }

    // Extract userId from Authorization header (JWT token)
    const user = await extractUserFromRequest(request)
    const userId = user.userId

    // Get document bucket from environment or Parameter Store
    let documentBucket = process.env.ARTIFACT_BUCKET

    if (!documentBucket) {
      // Try Parameter Store
      try {
        const ssmClient = new SSMClient({ region })
        const projectName = process.env.PROJECT_NAME || 'strands-agent-chatbot'
        const environment = process.env.ENVIRONMENT || 'dev'
        const paramName = `/${projectName}/${environment}/agentcore/artifact-bucket`

        const paramResponse = await ssmClient.send(
          new GetParameterCommand({ Name: paramName })
        )

        documentBucket = paramResponse.Parameter?.Value

        if (!documentBucket) {
          throw new Error('Document bucket not configured')
        }
      } catch (error) {
        console.error('[DocumentDownload] Failed to get bucket from Parameter Store:', error)
        return NextResponse.json(
          { error: 'Document bucket not configured' },
          { status: 500 }
        )
      }
    }

    // Map tool type to document type (for S3 path) and file extension
    const toolTypeConfig: Record<string, { docType: string; extension: string }> = {
      'word_document': { docType: 'word', extension: '.docx' },
      'word': { docType: 'word', extension: '.docx' },
      'excel_spreadsheet': { docType: 'excel', extension: '.xlsx' },
      'excel': { docType: 'excel', extension: '.xlsx' },
      'powerpoint_presentation': { docType: 'powerpoint', extension: '.pptx' },
      'powerpoint': { docType: 'powerpoint', extension: '.pptx' },
      'image': { docType: 'image', extension: '' },  // PNG files already have extension
      'diagram': { docType: 'image', extension: '' },  // Diagrams stored as images
      'code_output': { docType: 'code-output', extension: '' },
      'code-output': { docType: 'code-output', extension: '' }
    }

    const config = toolTypeConfig[toolType] || { docType: toolType, extension: '' }
    const documentType = config.docType

    // Add extension if filename doesn't have one
    let finalFilename = filename
    if (config.extension && !filename.toLowerCase().endsWith(config.extension)) {
      finalFilename = `${filename}${config.extension}`
    }

    // PowerPoint outputs are published into the canonical session Workspace.
    // Other document tools retain their existing namespaces until migrated.
    const objectKey = documentType === 'powerpoint'
      ? `code-interpreter-workspace/${codeInterpreterWorkspaceId(userId, sessionId)}/artifacts/powerpoint/${finalFilename}`
      : `documents/${userId}/${sessionId}/${documentType}/${finalFilename}`
    const s3Key = `s3://${documentBucket}/${objectKey}`

    console.log(`[DocumentDownload] Reconstructed S3 key: ${s3Key}`)

    return NextResponse.json({ s3Key })
  } catch (error) {
    console.error('[DocumentDownload] Error:', error)
    return NextResponse.json(
      { error: 'Failed to generate download path' },
      { status: 500 }
    )
  }
}
