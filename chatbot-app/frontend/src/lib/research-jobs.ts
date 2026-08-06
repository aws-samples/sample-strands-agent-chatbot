import fs from 'fs'
import path from 'path'
import {
  DynamoDBClient,
  QueryCommand,
} from '@aws-sdk/client-dynamodb'
import { S3Client, GetObjectCommand } from '@aws-sdk/client-s3'
import { unmarshall } from '@aws-sdk/util-dynamodb'

const IS_LOCAL = process.env.NEXT_PUBLIC_AGENTCORE_LOCAL === 'true'
const AWS_REGION = process.env.AWS_REGION || process.env.NEXT_PUBLIC_AWS_REGION || 'us-west-2'
const TABLE_NAME = process.env.DYNAMODB_USERS_TABLE || 'strands-agent-chatbot-users-v2'

export type ResearchJobStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'delivering'
  | 'delivered'
  | 'error'

export interface ResearchJob {
  jobId: string
  sessionId: string
  userId: string
  artifactId: string
  plan: string
  status: ResearchJobStatus
  createdAt: string
  updatedAt: string
  startedAt?: string
  completedAt?: string
  deliveredAt?: string
  error?: string
  deliveryError?: string
  progress?: {
    stepNumber: number
    content: string
  }
  artifact?: Record<string, any>
  artifactBucket?: string
  artifactS3Key?: string
  artifactPath?: string
}

function validSessionId(sessionId: string): boolean {
  return /^[a-zA-Z0-9_-]+$/.test(sessionId)
}

async function bodyToString(body: any): Promise<string> {
  if (!body) return ''
  if (typeof body.transformToString === 'function') {
    return body.transformToString()
  }
  const chunks: Buffer[] = []
  for await (const chunk of body) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk))
  }
  return Buffer.concat(chunks).toString('utf-8')
}

async function hydrateArtifact(job: ResearchJob): Promise<ResearchJob> {
  if (!job.artifact) return job

  let content = ''
  if (IS_LOCAL || job.userId === 'anonymous' || !!job.artifactPath) {
    if (!validSessionId(job.sessionId) || !/^[a-f0-9]+$/i.test(job.jobId)) return job
    const sessionsDir = path.resolve(process.cwd(), '..', 'agentcore', 'sessions')
    const reportPath = path.resolve(
      sessionsDir,
      `session_${job.sessionId}`,
      'research_jobs',
      `${job.jobId}.md`,
    )
    if (reportPath.startsWith(sessionsDir + path.sep) && fs.existsSync(reportPath)) {
      content = fs.readFileSync(reportPath, 'utf-8')
    }
  } else if (job.artifactBucket && job.artifactS3Key) {
    const response = await new S3Client({ region: AWS_REGION }).send(
      new GetObjectCommand({
        Bucket: job.artifactBucket,
        Key: job.artifactS3Key,
      }),
    )
    content = await bodyToString(response.Body)
  }

  return {
    ...job,
    artifact: {
      ...job.artifact,
      content,
    },
  }
}

function readLocalJobs(sessionId: string, userId: string): ResearchJob[] {
  if (!validSessionId(sessionId)) return []
  const sessionsDir = path.resolve(process.cwd(), '..', 'agentcore', 'sessions')
  const jobsDir = path.resolve(sessionsDir, `session_${sessionId}`, 'research_jobs')
  if (!jobsDir.startsWith(sessionsDir + path.sep) || !fs.existsSync(jobsDir)) return []

  return fs.readdirSync(jobsDir)
    .filter(name => /^[a-f0-9]+\.json$/i.test(name))
    .flatMap(name => {
      try {
        const job = JSON.parse(fs.readFileSync(path.join(jobsDir, name), 'utf-8'))
        if (job.sessionId !== sessionId) return []
        if (userId !== 'anonymous' && job.userId !== userId) return []
        return [job as ResearchJob]
      } catch {
        return []
      }
    })
}

async function readCloudJobs(sessionId: string, userId: string): Promise<ResearchJob[]> {
  const client = new DynamoDBClient({ region: AWS_REGION })
  const jobs: ResearchJob[] = []
  let exclusiveStartKey: Record<string, any> | undefined
  do {
    const response = await client.send(new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: 'userId = :userId AND begins_with(sk, :prefix)',
      ExpressionAttributeValues: {
        ':userId': { S: userId },
        ':prefix': { S: `RESEARCH_JOB#${sessionId}#` },
      },
      ConsistentRead: true,
      ExclusiveStartKey: exclusiveStartKey,
    }))
    jobs.push(...(response.Items || []).map(item => unmarshall(item) as ResearchJob))
    exclusiveStartKey = response.LastEvaluatedKey
  } while (exclusiveStartKey)
  return jobs
}

export async function listResearchJobs(
  userId: string,
  sessionId: string,
  options: { includeContent?: boolean } = {},
): Promise<ResearchJob[]> {
  const jobs = (IS_LOCAL || userId === 'anonymous')
    ? readLocalJobs(sessionId, userId)
    : await readCloudJobs(sessionId, userId)

  if (!options.includeContent) {
    return jobs.sort((a, b) => a.createdAt.localeCompare(b.createdAt))
  }

  const hydrated = await Promise.all(jobs.map(async job => {
    try {
      return await hydrateArtifact(job)
    } catch (error) {
      console.error(`[ResearchJobs] Failed to hydrate ${job.jobId}:`, error)
      return job
    }
  }))
  return hydrated.sort((a, b) => a.createdAt.localeCompare(b.createdAt))
}

export function completedResearchArtifacts(jobs: ResearchJob[]): any[] {
  return jobs
    .filter(job => ['completed', 'delivering', 'delivered'].includes(job.status))
    .map(job => job.artifact)
    .filter(artifact => artifact && typeof artifact.content === 'string' && artifact.content.length > 0)
}
