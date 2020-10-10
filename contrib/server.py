#!/usr/bin/env python3
# coding=utf-8

# Shim to enable faster testing than GCF
# Run like this:
# APIKEY=testkey TELEGRAM_TOKEN="123:abc" JOIN_LINK="https://t.me/404" \
#       pipenv run ./server.py

from os import environ

from flask import Flask, request

from main import webhook

environ["X_GOOGLE_FUNCTION_VERSION"] = "0"

app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
@app.route("/<path:path>", methods=["GET", "POST"])
def thing(*args, **kwargs):
    return webhook(request)


if __name__ == "__main__":
    app.run(debug=True)
