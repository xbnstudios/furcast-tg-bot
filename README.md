# FurCast Telegram Bot

Runs with Google Cloud Functions

## How to use

Set up a GCP project for Cloud Functions, and set up the gcloud tool with a
configuration named 'xbn'.

```bash
$ gcloud beta functions deploy furcast-tg-bot --runtime python37 --trigger-http \
    --entry-point webhook --configuration xbn \
    --set-env-vars "JOIN_LINK=$JOIN_LINK,TELEGRAM_TOKEN=$TELEGRAM_TOKEN"
```

From the output, get httpsTrigger.url, and put it into the next command:
```bash
$ curl "https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook?url=$TRIGGER_URL
```

To see configured webhooks,
```bash
$ curl "https://api.telegram.org/bot$TELEGRAM_TOKEN/getWebhookInfo"
```
