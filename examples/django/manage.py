"""Django: ``runserver`` registers the development server in Consul.

From this directory, with an agent on localhost:

    pip install "consulwire[django]"
    CONSUL_HOST=localhost python manage.py runserver 0.0.0.0:8000

In production, gunicorn serves the project instead, and the hooks of ``gunicorn.conf.py``
register it:

    pip install "consulwire[django]" gunicorn
    CONSUL_HOST=localhost gunicorn
"""

from __future__ import annotations

import os
import sys

from django.core.management import execute_from_command_line

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "my_project.settings")

    execute_from_command_line(sys.argv)
