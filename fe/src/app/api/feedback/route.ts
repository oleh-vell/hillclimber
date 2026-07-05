import { NextRequest, NextResponse } from "next/server";

import { sendMessage } from "@/lib/telegram";

const MAX_MESSAGE_LENGTH = 2000;

function asString(body: unknown, key: string): unknown {
  return typeof body === "object" && body !== null && key in body
    ? (body as Record<string, unknown>)[key]
    : undefined;
}

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Request body must be JSON" }, { status: 400 });
  }

  const message = asString(body, "message");
  if (typeof message !== "string" || message.trim() === "") {
    return NextResponse.json(
      { error: "'message' must be a non-empty string" },
      { status: 400 },
    );
  }
  if (message.length > MAX_MESSAGE_LENGTH) {
    return NextResponse.json(
      { error: `Feedback too long (max ${MAX_MESSAGE_LENGTH} characters)` },
      { status: 413 },
    );
  }

  const lines = ["💬 New hillclimber feedback", "", message.trim()];
  try {
    await sendMessage(lines.join("\n"));
  } catch (err) {
    console.error("Telegram feedback delivery failed", err);
    return NextResponse.json(
      { error: "Failed to deliver feedback — try again shortly" },
      { status: 502 },
    );
  }

  return NextResponse.json({ ok: true }, { status: 201 });
}
