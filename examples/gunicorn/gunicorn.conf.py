"""gunicorn's configuration: the hooks register the application once, from the main process.

From this directory, with an agent on localhost:

    pip install "consulwire[flask]" gunicorn
    CONSUL_HOST=localhost gunicorn
"""

from __future__ import annotations

import logging

from consulwire.integrations.gunicorn import ConsulHooks

logging.basicConfig(level=logging.INFO)

wsgi_app = "app:application"
bind = "0.0.0.0:8000"
workers = 2
accesslog = "-"

consul_hooks = ConsulHooks(name="gunicorn-example")

when_ready = consul_hooks.when_ready
on_exit = consul_hooks.on_exit
