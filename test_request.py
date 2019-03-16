#!/usr/bin/env python3

# Send a test request to the bot

import json
import requests

webhook = "http://127.0.0.1:5000/furcast-tg-bot"
apikey = "testkey"

instr = (
    b'{"update_id":1234,\n"message":{"message_id":456,"from":{"id":'
    b'789,"is_bot":false,"first_name":"John",'
    b'"username":"johnsmith","language_code":"en"},"chat":{"id":'
    b'789,"first_name":"John","username"'
    b':"johnsmith","type":"private"},"date":1552697790,"text":"/start",'
    b'"entities":[{"offset":0,"length":6,"type":"bot_command"}]}}'
)

instruct = json.loads(instr)

if __name__ == "__main__":
    r = requests.post("{}?apikey={}".format(webhook, apikey), json=instruct)

    print(r)
    print(r.text)
