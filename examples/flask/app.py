"""Flask: ``application.run()`` registers the application in Consul.

From this directory, with an agent on localhost:

    pip install "consulwire[flask]"
    CONSUL_HOST=localhost python app.py
"""

from __future__ import annotations

import logging

import flask

from consulwire.integrations.flask import ConsulExtension

application = flask.Flask(__name__)
ConsulExtension(application, name="flask-example")


@application.get("/hello")
def hello() -> str:
    return "hello"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    application.run(host="0.0.0.0", port=8000)
