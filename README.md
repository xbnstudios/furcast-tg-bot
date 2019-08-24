# FurCast Telegram Bot

Runs with Google Cloud Functions

## How to use

Set up a GCP project for Cloud Functions, and set up the gcloud tool with a
configuration named 'xbn'.
Generate a random alphanumeric token to be used as an API key for the bot, eg:
```bash
APIKEY=$(tr -cd '[:alnum:]'</dev/urandom|fold -w32|head -n1)
```

```bash
gcloud beta functions deploy furcast-tg-bot --runtime python37 --trigger http \
    --entry-point webhook --memory 128M --timeout 3s --configuration xbn \
    --set-env-vars "JOIN_LINK=$JOIN_LINK,TELEGRAM_TOKEN=$TELEGRAM_TOKEN,APIKEY=$APIKEY"
```

From the output, get httpsTrigger.url, and put it into the next command:
```bash
curl "https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook?url=$TRIGGER_URL&apikey=$APIKEY"
```

### Helpful stuff:
```bash
# See configured webhooks for bot
curl "https://api.telegram.org/bot$TELEGRAM_TOKEN/getWebhookInfo"
# See currently running version
curl "$TRIGGER_URL?apikey=$APIKEY&version"
# Re-deploy with the same settings,
gcloud beta functions deploy furcast-tg-bot --configuration xbn
```
