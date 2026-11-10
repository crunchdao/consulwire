"""Settings of the project, for development."""

from consulwire.integrations.django import ConsulSettings

SECRET_KEY = "a0b1c2d3e4f5g6h7i8j9"
DEBUG = True

# Consul checks http://host.docker.internal:8000/health: Django must accept that host.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "host.docker.internal"]

INSTALLED_APPS = [
    "consulwire.integrations.django",  # before staticfiles, which has a runserver of its own
    "django.contrib.staticfiles",
]

ROOT_URLCONF = "my_project.urls"
WSGI_APPLICATION = "my_project.wsgi.application"
STATIC_URL = "static/"

# The address the agent, in Docker, reaches the development server at.
CONSULWIRE: ConsulSettings = {"name": "django-example", "address": "host.docker.internal"}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"consulwire": {"handlers": ["console"], "level": "INFO"}},
}
