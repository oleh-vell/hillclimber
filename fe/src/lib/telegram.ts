// Thin wrapper over the Telegram Bot API. Used by /api/feedback to relay CLI
// user feedback to Oleh's chat.

const API_BASE = "https://api.telegram.org";

interface SendMessageResult {
  message_id: number;
}

interface TelegramResponse<T> {
  ok: boolean;
  result?: T;
  description?: string;
}

function token(): string {
  const t = process.env.TELEGRAM_BOT_TOKEN;
  if (!t) throw new Error("TELEGRAM_BOT_TOKEN is not set");
  return t;
}

function chatId(): string {
  const c = process.env.TELEGRAM_CHAT_ID;
  if (!c) throw new Error("TELEGRAM_CHAT_ID is not set");
  return c;
}

/**
 * Send a message to Oleh's chat. Returns the new message's id.
 */
export async function sendMessage(text: string): Promise<number> {
  const res = await fetch(`${API_BASE}/bot${token()}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId(), text }),
  });

  const data = (await res.json()) as TelegramResponse<SendMessageResult>;
  if (!data.ok || !data.result) {
    throw new Error(`Telegram sendMessage failed: ${data.description ?? res.status}`);
  }
  return data.result.message_id;
}
