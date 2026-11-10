"""Django integration: ``runserver`` registers the development server in Consul.

In production, Django runs under a server like gunicorn, whose hooks register it:
:mod:`consulwire.integrations.gunicorn`. This app covers ``python manage.py runserver``.

Example:
    In the settings:

    ```python
    INSTALLED_APPS = [
        "consulwire.integrations.django",  # before the apps overriding runserver
        "django.contrib.staticfiles",
        ...,
    ]

    CONSULWIRE = {"name": "my-service"}
    ```
"""

from __future__ import annotations

from consulwire.integrations.django._registration import ConsulSettings

__all__ = ["ConsulSettings"]
