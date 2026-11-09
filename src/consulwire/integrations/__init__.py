"""Register web applications in Consul, one module per framework or server.

The framework modules need their framework, installed with the matching extra: ``consulwire[flask]``
for :mod:`consulwire.integrations.flask`, ``consulwire[fastapi]`` for
:mod:`consulwire.integrations.fastapi`. :mod:`consulwire.integrations.gunicorn` needs none: it only
receives gunicorn's objects.
"""
