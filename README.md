# FurCast Telegram Bot

Redone for Google Cloud Functions

See https://seminar.io/2018/09/03/building-serverless-telegram-bot/

## How to use

```
$ gcloud beta functions deploy furcast-tg-bot --set-env-vars "TELEGRAM_TOKEN=123:abc" \
    --configuration xbn --runtime python37 --trigger-http --entry-point webhook
```
From the output, get httpsTrigger.url, and put it into the next command:
```
curl "https://api.telegram.org/bot<TELEGRAM_TOKEN>/setWebhook?url=<TRIGGER_URL>
```
To see configured webhooks,
```
curl "https://api.telegram.org/bot<TELEGRAM_TOKEN>/getWebhookInfo"
```
