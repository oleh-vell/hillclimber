# Connecting the Telegram feedback bot

The `POST /api/feedback` route relays every `hillclimber feedback "..."` message
to a Telegram chat. To make that work it needs two environment variables on the
Vercel project (`hillclimber`):

| Variable | What it is |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | The bot's API token from BotFather |
| `TELEGRAM_CHAT_ID` | The chat that should receive feedback (your DM, or a group) |

Both are read in `fe/src/lib/telegram.ts`. Without them the endpoint returns
`502 Failed to deliver feedback`.

## 1. Create the bot and get the token

1. In Telegram, open a chat with **[@BotFather](https://t.me/BotFather)**.
2. Send `/newbot`.
3. Give it a display name (e.g. `Hillclimber Feedback`) and a username ending
   in `bot` (e.g. `hillclimber_feedback_bot`).
4. BotFather replies with a token like `123456789:AAE...xyz`. **That is your
   `TELEGRAM_BOT_TOKEN`.** Keep it secret.

## 2. Get the chat ID

The bot can only message a chat that has interacted with it first.

**For a direct message to yourself:**

1. Open your new bot in Telegram and press **Start** (or send it any message).
2. In a terminal, fetch recent updates (replace `<TOKEN>`):

   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python3 -m json.tool
   ```

3. Find `"chat": { "id": 123456789, ... }` in the output. That number is your
   `TELEGRAM_CHAT_ID`. (For a personal chat it's a positive integer.)

   > Shortcut: message **[@userinfobot](https://t.me/userinfobot)** and it
   > replies with your numeric ID.

**For a group instead:**

1. Add the bot to the group.
2. Send a message in the group, then run the same `getUpdates` call.
3. The group `chat.id` is a **negative** number (e.g. `-1001234567890`) — use it
   verbatim, minus sign included.

## 3. Add the variables to Vercel

Either via CLI (run from `fe/`) …

```bash
cd fe

# You'll be prompted to paste each value; select Production (and Preview if you
# want feedback from preview deploys too).
vercel env add TELEGRAM_BOT_TOKEN production
vercel env add TELEGRAM_CHAT_ID production
```

… or via the dashboard: **Vercel → hillclimber → Settings → Environment
Variables → Add**, one row per variable, scoped to **Production**.

## 4. Redeploy

Environment variables are baked in at deploy time, so a redeploy is required
after adding them:

```bash
cd fe
vercel --prod
```

## 5. Test it

From the repo root, point the CLI at the deployed endpoint and send a test
message:

```bash
HILLCLIMBER_FEEDBACK_URL="https://fe-two-gamma.vercel.app/api/feedback" \
  uv run hillclimber feedback "test from the CLI"
```

You should see `✓ feedback sent — thank you!` and a `💬 New hillclimber
feedback` message arrive in your Telegram chat.

Or hit the endpoint directly:

```bash
curl -X POST "https://fe-two-gamma.vercel.app/api/feedback" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello from curl"}'
# → {"ok":true}
```

## 6. (Optional) Bake the URL into the CLI

Right now `DEFAULT_FEEDBACK_URL` in
`src/hillclimber/cli/commands/feedback.py` still points at the unregistered
`https://hillclimber.dev/api/feedback`, so users must set
`HILLCLIMBER_FEEDBACK_URL` by hand. Once you're happy with the deployed URL,
update that constant to the live endpoint (or a custom domain) so the bare
`hillclimber feedback` command works out of the box.

## Troubleshooting

- **`502 Failed to deliver feedback`** — a variable is missing or wrong, or you
  didn't redeploy. Check `vercel env ls` and the function logs
  (`vercel logs <deployment-url>`).
- **`chat not found` in logs** — the `TELEGRAM_CHAT_ID` is wrong, or the chat
  never messaged the bot first.
- **Nothing in `getUpdates`** — make sure you pressed Start / sent a message
  *after* creating the bot; updates expire, so send a fresh one.
