import { randomUUID } from "node:crypto";
import { config, logger } from "./config.js";

export interface ImageData {
  format: string;
  data: string;
  title?: string;
}

export interface FileData {
  filename: string;
  mimeType: string;
  data: string;
  s3Url?: string;
}

export interface LocationData {
  lat: number;
  lng: number;
  label?: string;
}

export interface InterruptData {
  id: string;
  name: string;
  reason?: Record<string, unknown>;
}

export interface AgentResponse {
  text: string;
  images: ImageData[];
  files: FileData[];
  locations: LocationData[];
  artifacts: string[];
  interrupt?: InterruptData;
  error?: string;
}

type ContentPart =
  | { type: "text"; text: string }
  | { type: "binary"; mimeType: string; data: string; filename: string };

export type ProgressCallback = (status: string) => void;

let cachedToken: string | null = null;
let tokenExpiry = 0;

async function getAccessToken(): Promise<string> {
  if (cachedToken && Date.now() / 1000 < tokenExpiry) {
    return cachedToken;
  }

  const credentials = Buffer.from(
    `${config.m2mClientId}:${config.m2mClientSecret}`,
  ).toString("base64");

  const resp = await fetch(config.cognitoTokenUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Authorization: `Basic ${credentials}`,
    },
    body: "grant_type=client_credentials&scope=agentcore/invoke",
  });

  if (!resp.ok) {
    throw new Error(`Cognito token request failed: ${resp.status}`);
  }

  const data = (await resp.json()) as {
    access_token: string;
    expires_in?: number;
  };
  cachedToken = data.access_token;
  tokenExpiry = Date.now() / 1000 + (data.expires_in ?? 3600) - 60;
  return cachedToken;
}

const sessionEpochs = new Map<number, number>();
const selectedModels = new Map<number, string>();
export const DEFAULT_MODEL_ID = "openai.gpt-5.6-terra";

export function resetSession(chatId: number): void {
  sessionEpochs.set(chatId, Date.now());
}

export function setModel(chatId: number, modelId: string): void {
  selectedModels.set(chatId, modelId);
}

export function getModel(chatId: number): string {
  return selectedModels.get(chatId) ?? DEFAULT_MODEL_ID;
}

function buildSessionId(chatId: number): string {
  const epoch = sessionEpochs.get(chatId);
  const suffix = epoch ? `_${epoch}` : "";
  const base = `tg_${chatId}${suffix}`;
  return base.length >= 33 ? base : base + "_".repeat(33 - base.length);
}

function resolveUserId(chatId: number): string {
  if (config.ownerUserId) return config.ownerUserId;
  return `tg_user_${chatId}`;
}


function buildPayload(
  chatId: number,
  content: string | ContentPart[],
  sessionId: string,
  resume?: Array<{
    interruptId: string;
    status: "resolved" | "cancelled";
    payload?: unknown;
  }>,
) {
  const runId = randomUUID();
  const messages =
    typeof content === "string"
      ? [{ id: "msg-1", role: "user", content }]
      : [{ id: "msg-1", role: "user", content }];

  return {
    threadId: sessionId,
    runId,
    messages,
    tools: [],
    context: [],
    ...(resume && resume.length > 0 ? { resume } : {}),
    state: {
      user_id: resolveUserId(chatId),
      channel: "telegram",
      allow_user_federation: false,
      model_id: getModel(chatId),
    },
  };
}

export async function invokeAgent(
  chatId: number,
  content: string | ContentPart[],
  onProgress?: ProgressCallback,
  resume?: Array<{
    interruptId: string;
    status: "resolved" | "cancelled";
    payload?: unknown;
  }>,
): Promise<AgentResponse> {
  const sessionId = buildSessionId(chatId);
  const token = await getAccessToken();
  const payload = buildPayload(chatId, content, sessionId, resume);

  logger.info({ chatId, sessionId }, "Invoking AgentCore");

  const resp = await fetch(config.runtimeInvocationUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      Authorization: `Bearer ${token}`,
      "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sessionId,
    },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(600_000),
  });

  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    logger.error({ status: resp.status, body: body.slice(0, 500) }, "AgentCore error");
    return { text: "", images: [], files: [], locations: [], artifacts: [], error: `AgentCore returned ${resp.status}` };
  }

  logger.info({ status: resp.status }, "AgentCore responded, parsing SSE");
  return collectSseResponse(resp, onProgress);
}

export function buildContentParts(
  text: string | null,
  attachments: { mimeType: string; data: string; filename: string }[],
): string | ContentPart[] {
  if (attachments.length === 0) {
    return text ?? "";
  }

  const parts: ContentPart[] = [];
  if (text) {
    parts.push({ type: "text", text });
  }
  for (const att of attachments) {
    parts.push({ type: "binary", ...att });
  }
  return parts;
}

async function collectSseResponse(
  resp: Response,
  onProgress?: ProgressCallback,
): Promise<AgentResponse> {
  const textParts: string[] = [];
  const images: ImageData[] = [];
  const files: FileData[] = [];
  const locations: LocationData[] = [];
  const artifacts: string[] = [];
  let interrupt: InterruptData | undefined;
  let error: string | undefined;
  let finished = false;

  const reader = resp.body?.getReader();
  if (!reader) return { text: "", images: [], files: [], locations: [], artifacts: [], error: "No response body" };

  const decoder = new TextDecoder();
  let buffer = "";

  while (!finished) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;

      let event: Record<string, unknown>;
      try {
        event = JSON.parse(line.slice(6));
      } catch {
        continue;
      }

      const type = event.type as string | undefined;

      if (type === "TEXT_MESSAGE_CONTENT" && event.delta) {
        textParts.push(event.delta as string);
      } else if (type === "TOOL_CALL_START") {
        onProgress?.("thinking");
      } else if (type === "TOOL_CALL_RESULT") {
        extractMediaFromToolResult(event, images, files, locations, artifacts);
      } else if (type === "CUSTOM") {
        interrupt = handleCustomEvent(event, images, onProgress) ?? interrupt;
      } else if (type === "RUN_FINISHED") {
        finished = true;
        break;
      } else if (type === "RUN_ERROR") {
        error = (event.message as string) ?? "Unknown error";
        finished = true;
        break;
      }
    }
  }

  reader.cancel().catch(() => {});

  const text = textParts.join("");
  logger.info({ textLen: text.length, images: images.length, files: files.length, artifacts: artifacts.length }, "SSE parsing complete");
  if (!text && !error && !interrupt && images.length === 0 && files.length === 0 && locations.length === 0 && artifacts.length === 0) {
    return { text: "I processed your message but have no response to show.", images, files, locations, artifacts };
  }
  return { text, images, files, locations, artifacts, interrupt, error };
}

function extractMediaFromToolResult(
  event: Record<string, unknown>,
  images: ImageData[],
  files: FileData[],
  locations: LocationData[],
  artifacts: string[],
): void {
  let content: Record<string, unknown> | undefined;
  try {
    const raw = event.content as string | Record<string, unknown>;
    content = typeof raw === "string" ? JSON.parse(raw) : raw;
  } catch {
    return;
  }
  if (!content) return;

  // Extract inline base64 images
  const imgArray = content.images as { format?: string; data?: string }[] | undefined;
  if (Array.isArray(imgArray)) {
    for (const img of imgArray) {
      if (img.data) {
        images.push({ format: img.format ?? "png", data: img.data });
      }
    }
  }

  // Extract S3 file artifacts (PPTX, DOCX, XLSX)
  const metadata = content.metadata as Record<string, unknown> | undefined;
  if (metadata?.s3_url && metadata?.filename) {
    files.push({
      filename: metadata.filename as string,
      mimeType: guessMimeType(metadata.filename as string),
      data: "",
      s3Url: metadata.s3_url as string,
    });
  }

  // Extract tool result text — research reports go to artifacts, map data to locations
  const resultStr = content.result as string | undefined;
  if (resultStr && typeof resultStr === "string" && resultStr.length > 500) {
    // Long result text = research report / code agent summary → artifact
    try {
      JSON.parse(resultStr);
      // If it parses as JSON, it's structured data — don't treat as artifact
    } catch {
      // Plain text / markdown — treat as artifact
      artifacts.push(resultStr);
    }
  }

  if (resultStr) {
    try {
      const result = typeof resultStr === "string" ? JSON.parse(resultStr) : resultStr;
      const mapData = (result as Record<string, unknown>).map_data as Record<string, unknown> | undefined;
      if (mapData) {
        const markers = mapData.markers as { lat: number; lng: number; label?: string }[] | undefined;
        if (Array.isArray(markers)) {
          for (const m of markers) {
            if (typeof m.lat === "number" && typeof m.lng === "number") {
              locations.push({ lat: m.lat, lng: m.lng, label: m.label });
            }
          }
        }
        const directions = mapData.directions as Record<string, unknown> | undefined;
        if (directions) {
          const origin = directions.origin as { lat: number; lng: number } | undefined;
          const dest = directions.destination as { lat: number; lng: number } | undefined;
          if (origin) locations.push({ lat: origin.lat, lng: origin.lng, label: "Origin" });
          if (dest) locations.push({ lat: dest.lat, lng: dest.lng, label: "Destination" });
        }
      }
    } catch { /* not JSON, skip */ }
  }
}

const MIME_MAP: Record<string, string> = {
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".pdf": "application/pdf",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
};

function guessMimeType(filename: string): string {
  const ext = filename.slice(filename.lastIndexOf(".")).toLowerCase();
  return MIME_MAP[ext] ?? "application/octet-stream";
}

function handleCustomEvent(
  event: Record<string, unknown>,
  images: ImageData[],
  onProgress?: ProgressCallback,
): InterruptData | undefined {
  const name = event.name as string | undefined;
  if (!name) return;

  if (name === "interrupt") {
    const value = event.value as Record<string, unknown> | undefined;
    const interrupts = value?.interrupts as { id: string; name: string; reason?: Record<string, unknown> }[] | undefined;
    if (Array.isArray(interrupts) && interrupts.length > 0) {
      return interrupts[0];
    }
  } else if (
    name === "thinking" ||
    name === "browser_progress" ||
    name === "research_progress" ||
    name === "code_agent_heartbeat"
  ) {
    onProgress?.(name);
  } else if (name === "complete_metadata") {
    const value = event.value as Record<string, unknown> | undefined;
    if (value) {
      const imgs = value.images as { format?: string; data?: string }[] | undefined;
      if (Array.isArray(imgs)) {
        for (const img of imgs) {
          if (img.data) {
            images.push({ format: img.format ?? "png", data: img.data });
          }
        }
      }
    }
  }
  return undefined;
}
