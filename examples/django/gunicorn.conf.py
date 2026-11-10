"""gunicorn's configuration, for production: the hooks register the project, not ``runserver``."""

from __future__ import annotations

import logging

from consulwire.integrations.gunicorn import ConsulHooks

logging.basicConfig(level=logging.INFO)

wsgi_app = "my_project.wsgi:application"
bind = "0.0.0.0:8000"
workers = 2
accesslog = "-"

# Django answers 400 to a host missing from ALLOWED_HOSTS: register one it allows.
consul_hooks = ConsulHooks(name="django-example", address="host.docker.internal")

when_ready = consul_hooks.when_ready
on_exit = consul_hooks.on_exit
