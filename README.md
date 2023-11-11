# FurCast Telegram Bot

* Gates entry to the main FurCast group, to reduce bot activity
* Information and utility functions like `/next [show] [tz]`

Runs both as a poll bot and a free Google Cloud Function. The poll bot handles
most things, especially those requiring state like `/next pin`. The GCF copy
handles external events like Now Playing announcements.

## Commands
```
next - See next scheduled show, e.g. "/next fnt" or "/next fc Europe/London"
topic - Request chat topic, e.g. "/topic Not My Cup Of Legs"
report - Get admin attention. Reply to a message with e.g. "/report Spambot!"
```
### Other commands
Don't tell BotFather.
```
chatinfo - List the chat ID
newlink [slug] [link [link...]] - (Admin group) Revoke invite link(s)
next [slug] pin - Pin a continuously updated countdown message
start - (PM) Print some help & suggest /join. Prompted by TG client.
join - (PM) Request a group invite
stopic - Silently set the topic (delete command message)
version - Print the source link and GCF version if available
```

## How to use

* Generate a random alphanumeric token to be used as an API key for the bot, eg:

  ```bash
  APIKEY=$(tr -cd '[:alnum:]'</dev/urandom|fold -w32|head -n1)
  ```
* Create a private group (or make your group private). If you don't want the bot
  to have admin, generate an invite link for `$JOIN_LINK` below
* Create a bot, eg `@furcastbot`, to run this, and save the bot token for
  `$TELEGRAM_TOKEN` below
* Create a channel with the @ you want, eg. `@furcastfm`, and write a message
  instructing users to talk to the bot for entry to the group.
* If using poll bot, assuming linux account name is `bots`:
  * Copy config.toml.example to config.toml and edit
  * `virtualenv -p python3 venv`
  * `. venv/bin/activate`
  * `pip install --upgrade .`
  * `deactivate`
  * `venv/bin/furcastbot` to verify functionality
  * For `systemd`:
    * `sudo loginctl enable-linger bots`
    * `ln -s ../../../furcast-tg-bot/contrib/furcastbot.service
      ~/.config/systemd/user/furcastbot.service`
    * `systemctl --user daemon-reload`
    * `systemctl --user enable --now furcastbot`
  * For OpenBSD (and maybe other sysv-style init systems, we haven't checked):
    * `doas cp ../../../furcast-tg-bot/contrib/openbsd.rc /etc/rc.d/furcast_bot`
    * `doas chmod +x /etc/rc.d/furcast_bot`
    * `doas rcctl start furcast_bot`
    * `doas rcctl enable furcast_bot`
    * (If you want to run multiple copies of the bot, we recommend copying the rc script and fiddling with the variables, rather than symlinking and using `rcctl set service_name flags`, since the config file is not read from the flags.)

* For the GCP part:
  * Set up a
    [new GCP project](https://console.cloud.google.com/projectcreate?previousPage=%2Ffunctions%2Flist)
  * [Enable Cloud Functions](https://console.cloud.google.com/flows/enableapi?apiid=cloudfunctions)
    for the project
  * EITHER create a new function and manually configure it and upload the source, or continue:
  * Set up the Google Cloud SDK's gcloud tool with a configuration named 'xbn':

  ```bash
  gcloud config configurations create xbn
  gcloud auth login
  gcloud config set project xana-broadcasting
  ```

  * Deploy to GCF with the Google Cloud SDK (Repeat after code/config updates)

  ```bash
  gcloud functions deploy furcast-tg-bot --trigger-http --entry-point webhook \
    --memory 128M --timeout 5s --configuration xbn --set-env-vars "JOIN_LINK=error" \
    --runtime python311 --docker-registry=artifact-registry --set-build-env-vars=GOOGLE_FUNCTION_SOURCE=main.py
  ```

  * From the output, get `httpsTrigger.url`, and set the webhook in the telegram bot:

  ```bash
  curl "https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook?url=$TRIGGER_URL&apikey=$APIKEY"
  ```

### Helpful stuff:
```bash
# Re-deploy with the same settings,
gcloud functions deploy furcast-tg-bot --configuration xbn
```
