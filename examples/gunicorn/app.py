"""A Flask application under gunicorn: the hooks of ``gunicorn.conf.py`` register it.

Under gunicorn, the extension does not register: it only adds the health route, and hides the
health checks from the access log.
"""

from __future__ import annotations

import flask

from consulwire.integrations.flask import ConsulExtension

application = flask.Flask(__name__)
ConsulExtension(application, name="gunicorn-example")


@application.get("/hello")
def hello() -> str:
    return "hello"
