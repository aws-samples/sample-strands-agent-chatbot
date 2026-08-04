import type { Bot, Context } from "grammy";
import { config, logger } from "./config.js";
import { isDuplicate } from "./dedup.js";
import {
  invokeAgent,
  buildContentParts,
  resetSession,
  setModel,
  getModel,
  type ProgressCallback,
} from "./agentcore-client.js";
import { sendAgentResponse, getPendingInterruptId, clearPendingInterrupt } from "./response-sender.js";
import { downloadTelegramFile, getLargestPhoto } from "./media-handler.js";
import { InlineKeyboard } from "grammy";
import { bufferMessage, setFlushHandler, clearBusy } from "./inbound-buffer.js";
import { bufferPhotoGroup } from "./media-group-buffer.js";

const MODELS = [
  { id: "us.anthropic.claude-sonnet-5", label: "Sonnet 5" },
  { id: "us.anthropic.claude-haiku-4-5-20251001-v1:0", label: "Haiku 4.5" },
  { id: "us.anthropic.claude-opus-5", label: "Opus 5" },
  { id: "openai.gpt-5.6-sol", label: "GPT-5.6 Sol" },
  { id: "openai.gpt-5.6-terra", label: "GPT-5.6 Terra" },
  { id: "openai.gpt-5.6-luna", label: "GPT-5.6 Luna" },
] as const;

export function setupMessageHandlers(bot: Bot): void {
  setFlushHandler(handleBufferedMessage);
  bot.command("reset", handleReset);
  bot.command("model", handleModelCommand);
  bot.on("message:text", handleTextMessage);
  bot.on("message:photo", handlePhotoMessage);
  bot.on("message:document", handleDocumentMessage);
  bot.on("callback_query:data", handleCallbackQuery);
}

async function handleReset(ctx: Context): Promise<void> {
  const chatId = ctx.chat?.id;
  if (!chatId) return;
  resetSession(chatId);
  clearBusy(chatId);
  clearPendingInterrupt(chatId);
  await ctx.reply("Session reset. Starting fresh conversation.");
  logger.info({ chatId }, "Session reset");
}

async function handleModelCommand(ctx: Context): Promise<void> {
  const chatId = ctx.chat?.id;
  if (!chatId) return;

  const current = getModel(chatId);
  const keyboard = new InlineKeyboard();

  for (let i = 0; i < MODELS.length; i++) {
    const m = MODELS[i];
    const check = m.id === current ? " *" : "";
    keyboard.text(`${m.label}${check}`, `model:${i}`);
  }

  await ctx.reply("Select a model:", { reply_markup: keyboard });
}

async function handleTextMessage(ctx: Context): Promise<void> {
  if (!ctx.message?.text) return;
  if (ctx.message.text.startsWith("/")) return;
  if (!passesGuards(ctx)) return;

  bufferMessage({ ctx, text: ctx.message.text, attachments: [] });
}

async function handlePhotoMessage(ctx: Context): Promise<void> {
  const photos = ctx.message?.photo;
  if (!photos || photos.length === 0) return;
  if (!passesGuards(ctx)) return;

  const caption = ctx.message.caption ?? null;
  const fileId = getLargestPhoto(photos);
  const mediaGroupId = ctx.message.media_group_id;

  // Album: collect all photos in the group, download serially, then submit as one message.
  if (mediaGroupId) {
    bufferPhotoGroup(ctx, mediaGroupId, fileId, caption);
    return;
  }

  try {
    const media = await downloadTelegramFile(
      ctx.api,
      fileId,
      "image/jpeg",
      "photo.jpg",
    );
    bufferMessage({ ctx, text: caption, attachments: [media] });
  } catch (err) {
    logger.error({ err }, "Failed to download photo");
    await ctx.reply("Something went wrong. Please try again or use /reset to start a new session.");
  }
}

async function handleDocumentMessage(ctx: Context): Promise<void> {
  const doc = ctx.message?.document;
  if (!doc) return;
  if (!passesGuards(ctx)) return;

  const caption = ctx.message.caption ?? null;
  const mimeType = doc.mime_type ?? "application/octet-stream";
  const filename = doc.file_name ?? "document";
  const mediaGroupId = ctx.message.media_group_id;

  if (mediaGroupId) {
    bufferPhotoGroup(ctx, mediaGroupId, doc.file_id, caption, mimeType, filename);
    return;
  }

  try {
    const media = await downloadTelegramFile(
      ctx.api,
      doc.file_id,
      mimeType,
      filename,
    );
    bufferMessage({ ctx, text: caption, attachments: [media] });
  } catch (err) {
    logger.error({ err }, "Failed to download document");
    await ctx.reply("Something went wrong. Please try again or use /reset to start a new session.");
  }
}

async function handleCallbackQuery(ctx: Context): Promise<void> {
  const data = ctx.callbackQuery?.data;
  const chatId = ctx.chat?.id;
  if (!data || !chatId) return;

  if (data.startsWith("model:")) {
    const idx = parseInt(data.slice(6), 10);
    const model = MODELS[idx];
    if (!model) return;
    setModel(chatId, model.id);
    await ctx.answerCallbackQuery({ text: `Switched to ${model.label}` });
    if (ctx.callbackQuery?.message?.message_id) {
      await ctx.api.editMessageText(chatId, ctx.callbackQuery.message.message_id, `Model: ${model.label}`).catch(() => {});
    }
    logger.info({ chatId, model: model.id }, "Model changed");
    return;
  }

  if (!data.startsWith("int:")) return;

  const approved = data === "int:y";
  const interruptId = getPendingInterruptId(chatId);
  if (!interruptId) return;
  clearPendingInterrupt(chatId);

  await ctx.answerCallbackQuery({
    text: approved ? "Approved" : "Declined",
  });

  // Remove inline keyboard
  if (ctx.callbackQuery?.message?.message_id) {
    await ctx.api.editMessageReplyMarkup(chatId, ctx.callbackQuery.message.message_id, {
      reply_markup: { inline_keyboard: [] },
    }).catch(() => {});
  }

  logger.info({ chatId, interruptId, approved }, "Interrupt response");

  const onProgress: ProgressCallback = () => {
    ctx.api.sendChatAction(chatId, "typing").catch(() => {});
  };
  onProgress("start");

  const content = JSON.stringify([{ interruptResponse: { interruptId, response: approved ? "approved" : "declined" } }]);
  const agentResponse = await invokeAgent(chatId, content, onProgress);
  await sendAgentResponse(ctx.api, chatId, agentResponse);
}

function passesGuards(ctx: Context): boolean {
  const userId = ctx.from?.id;
  if (!userId) return false;

  if (
    config.allowedUserIds.length > 0 &&
    !config.allowedUserIds.includes(userId)
  ) {
    logger.debug({ userId }, "User not in allowlist");
    return false;
  }
  return true;
}

async function handleBufferedMessage(
  chatId: number,
  userId: number,
  text: string,
  attachments: { mimeType: string; data: string; filename: string }[],
  ctx: Context,
): Promise<void> {
  const messageId = `tg_${chatId}_${Date.now()}`;
  if (await isDuplicate(messageId)) return;

  logger.info({ chatId, userId, attachments: attachments.length }, "Processing buffered message");

  const onProgress: ProgressCallback = () => {
    ctx.api.sendChatAction(chatId, "typing").catch(() => {});
  };
  onProgress("start");

  const content = buildContentParts(
    text || (attachments.length > 0 ? "Analyze these files" : ""),
    attachments,
  );

  try {
    const response = await invokeAgent(chatId, content, onProgress);
    await sendAgentResponse(ctx.api, chatId, response);
  } catch (err) {
    logger.error({ err, chatId }, "Failed to process message");
    const isTimeout = err instanceof DOMException && err.name === "TimeoutError";
    const msg = isTimeout
      ? "The request timed out. Please try again or use /reset to start a new session."
      : "Something went wrong. Please try again or use /reset to start a new session.";
    await ctx.api.sendMessage(chatId, msg).catch(() => {});
  }
}
