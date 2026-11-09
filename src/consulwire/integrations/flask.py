"""Flask integration: one line, and ``application.run()`` registers the application in Consul.

Example:
    ```python
    application = flask.Flask(__name__)
    ConsulExtension(application, name="my-service")

    application.run(host="0.0.0.0", port=8000)
    ```
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, Final, cast

import flask
import flask.cli
from flask.helpers import get_debug_flag, get_load_dotenv
from werkzeug.exceptions import HTTPException
from werkzeug.serving import is_running_from_reloader

from consulwire._client import Consul
from consulwire._environment import find_url
from consulwire.integrations._common import (
    DEFAULT_CHECK_INTERVAL,
    DEFAULT_DEREGISTER_CRITICAL_AFTER,
    DEFAULT_HEALTH_PATH,
    health_check,
    hide_health_checks,
    resolve_address,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import timedelta

    from _typeshed.wsgi import StartResponse, WSGIEnvironment

__all__ = ["ConsulExtension"]

EXTENSION_NAME: Final = "consul"

ACCESS_LOGGERS: Final = ("werkzeug", "gunicorn.access")

FLASK_DEFAULT_PORT: Final = 5000
"""Port ``Flask.run`` listens on when neither its arguments nor ``SERVER_NAME`` give one."""

logger = logging.getLogger(__name__)


class ConsulExtension:
    """Register a Flask application in Consul when it starts, like Spring Cloud Consul does.

    Setting the extension up on an application:

    - makes ``application.run()`` register the application right before it listens: the port and
      host are the ones ``run`` uses, and the address is found automatically when the host is a
      wildcard like ``0.0.0.0``. With the reloader, only the process serving requests registers;
    - gives Consul a health route: the application's own ``GET`` route at ``health_path`` when it
      has one, otherwise one answering 200, added right before the registration, or before the
      first request under another server;
    - hides the health check requests from the werkzeug and gunicorn access logs, unless
      ``log_health_checks`` is set.

    Flask has no shutdown event, so the application is never deregistered: Consul removes an
    instance that stopped answering after ``deregister_critical_after``, and the next start
    replaces it, its ID being the service name.

    Registration is skipped, with a log line, when no agent is configured: no ``consul`` given, and
    neither ``CONSUL_HTTP_ADDR`` nor ``CONSUL_HOST`` set. Under gunicorn,
    :class:`consulwire.integrations.gunicorn.ConsulHooks` registers the application. Other servers
    that do not go through ``application.run()`` call :meth:`register` themselves.
    """

    name: str
    """Service name, also the instance ID: registering again replaces the instance."""

    health_path: str

    def __init__(  # noqa: PLR0913  # keyword-only options, like the other Flask extensions
        self,
        application: flask.Flask | None = None,
        *,
        name: str,
        health_path: str = DEFAULT_HEALTH_PATH,
        address: str | None = None,
        consul: Consul | None = None,
        check_interval: timedelta = DEFAULT_CHECK_INTERVAL,
        deregister_critical_after: timedelta | None = DEFAULT_DEREGISTER_CRITICAL_AFTER,
        tags: Sequence[str] = (),
        meta: Mapping[str, str] | None = None,
        log_health_checks: bool = False,
    ) -> None:
        """Create the extension, and set it up on the application when one is given.

        Args:
            application: Application to set up now. Otherwise, call :meth:`init_app` later.
            name: Name other services look this one up by.
            health_path: Route Consul checks. The application's own ``GET`` route is used when it
                has one, otherwise one answering 200 is added.
            address: Address other services connect to. By default, the host given to
                ``application.run()`` when it is a specific one, otherwise the local IP address the
                system uses to reach the agent.
            consul: Client of the agent. By default, one configured from the environment.
            check_interval: Time between two health checks.
            deregister_critical_after: Time after which Consul removes an instance whose health
                check keeps failing, ``None`` to keep it. Consul ignores values under a minute.
            tags: Service tags.
            meta: Service key and value pairs.
            log_health_checks: Keep the health check requests in the access logs.
        """
        self.name = name
        self.health_path = health_path

        self._address: str | None = address
        self._consul: Consul | None = consul
        self._check_interval: timedelta = check_interval
        self._deregister_critical_after: timedelta | None = deregister_critical_after
        self._tags: Sequence[str] = tags
        self._meta: Mapping[str, str] = meta or {}
        self._log_health_checks: bool = log_health_checks

        self._application: flask.Flask | None = None
        self._registered: bool = False
        self._health_route_lock: threading.Lock = threading.Lock()
        self._health_route_ready: bool = False

        if application is not None:
            self.init_app(application)

    @classmethod
    def of(cls, application: flask.Flask) -> ConsulExtension:
        """Return the extension set up on an application.

        Raises:
            LookupError: No consul extension is set up on the application.
            TypeError: Something else is stored under the extension name.
        """
        extension: object = application.extensions.get(EXTENSION_NAME)

        if extension is None:
            message = "no consul extension is set up on the application"
            raise LookupError(message)

        if not isinstance(extension, cls):
            kind = type(extension).__name__
            message = (
                f"application.extensions[{EXTENSION_NAME!r}] is a {kind}, not a {cls.__name__}"
            )
            raise TypeError(message)

        return extension

    def init_app(self, application: flask.Flask) -> None:
        """Set the extension up on an application.

        Raises:
            RuntimeError: This extension, or another consul extension, is already set up.
        """
        if self._application is not None or EXTENSION_NAME in application.extensions:
            message = "a consul extension is already set up"
            raise RuntimeError(message)

        if not self._log_health_checks:
            hide_health_checks(ACCESS_LOGGERS, self.health_path)

        self._wrap_run(application)
        self._wrap_wsgi_app(application)

        application.extensions[EXTENSION_NAME] = self
        self._application = application

    def register(self, *, port: int, address: str | None = None) -> bool:
        """Register the application. ``application.run()`` already does it.

        Adds the health route first when the application has none.

        Args:
            port: Port the application listens on.
            address: Address other services connect to. By default, the one given to the
                extension, otherwise the local IP address the system uses to reach the agent.

        Returns:
            Whether the application was registered: ``False`` when no agent is configured.

        Raises:
            RuntimeError: The extension is not set up on an application yet.
            ConsulError: The agent could not be reached, or refused the registration.
        """
        application = self._set_up_application()
        _ensure_health_route(application, self.health_path)

        consul = self._agent_client()
        if consul is None:
            logger.info("consul registration skipped, no agent configured - name=%s", self.name)
            return False

        address = resolve_address(address or self._address, None, consul.url)

        consul.agent.register_service(
            self.name,
            service_id=self.name,
            address=address,
            port=port,
            tags=self._tags,
            meta=self._meta,
            checks=[
                health_check(
                    address,
                    port,
                    self.health_path,
                    interval=self._check_interval,
                    deregister_critical_after=self._deregister_critical_after,
                ),
            ],
        )

        self._registered = True

        logger.info(
            "registered in consul - name=%s address=%s port=%d agent=%s",
            self.name,
            address,
            port,
            consul.url,
        )

        return True

    def deregister(self) -> None:
        """Deregister the application, if registered. Errors are raised.

        Nothing calls it: Flask has no shutdown event. A server with one, like gunicorn's
        ``on_exit`` hook, can.
        """
        if self._consul is None or not self._registered:
            return

        self._consul.agent.deregister_service(self.name)
        self._registered = False

        logger.info("deregistered from consul - name=%s", self.name)

    def _set_up_application(self) -> flask.Flask:
        if self._application is None:
            message = "the consul extension must be set up on an application before registering"
            raise RuntimeError(message)

        return self._application

    def _agent_client(self) -> Consul | None:
        if self._consul is None:
            url = find_url()
            if url is None:
                return None

            self._consul = Consul(url)

        return self._consul

    def _wrap_run(self, application: flask.Flask) -> None:
        run = application.run

        def run_registered(
            host: str | None = None,
            port: int | None = None,
            debug: bool | None = None,  # noqa: FBT001  # the signature of Flask.run
            load_dotenv: bool = True,  # noqa: FBT001, FBT002  # the signature of Flask.run
            **options: object,
        ) -> None:
            # `flask run` makes Flask.run return right away, without serving.
            if os.environ.get("FLASK_RUN_FROM_CLI") != "true":
                self._register_before_run(
                    application,
                    host=host,
                    port=port,
                    debug=debug,
                    load_dotenv=load_dotenv,
                    use_reloader=options.get("use_reloader"),
                )

            run(host, port, debug, load_dotenv, **options)

        # Replaced on this instance only, so the extension combines with any other one, and two
        # applications in the same process stay apart. Flask calls `run` only from user code.
        run_registered.__doc__ = run.__doc__
        application.run = run_registered  # type: ignore[method-assign]

    def _wrap_wsgi_app(self, application: flask.Flask) -> None:
        """Add the health route before the first request, for servers calling the application.

        Replaced on the instance like any WSGI middleware. Flask refuses new routes once a request
        was handled, and every route of the application is declared by then.
        """
        wsgi_app = application.wsgi_app

        def wsgi_app_with_health_route(
            environ: WSGIEnvironment,
            start_response: StartResponse,
        ) -> Iterable[bytes]:
            if not self._health_route_ready:
                with self._health_route_lock:
                    _ensure_health_route(application, self.health_path)
                    self._health_route_ready = True

            return wsgi_app(environ, start_response)

        application.wsgi_app = wsgi_app_with_health_route  # type: ignore[method-assign]

    def _register_before_run(  # noqa: PLR0913  # the arguments of Flask.run
        self,
        application: flask.Flask,
        *,
        host: str | None,
        port: int | None,
        debug: bool | None,
        load_dotenv: bool,
        use_reloader: object,
    ) -> None:
        """Register with the host and port ``Flask.run`` is about to use, by its own rules."""
        if get_load_dotenv(load_dotenv):
            # Flask.run loads them too, but only after this point, and CONSUL_HOST or FLASK_DEBUG
            # may come from them. Loading twice changes nothing: set variables are kept.
            _ = flask.cli.load_dotenv()

        if not _serves_requests(
            application, debug=debug, load_dotenv=load_dotenv, use_reloader=use_reloader
        ):
            return

        server_name_host, server_name_port = _server_name(application)
        listening_host = host or server_name_host
        listening_port = port if port is not None else server_name_port or FLASK_DEFAULT_PORT

        if listening_port == 0:
            _ensure_health_route(application, self.health_path)
            logger.warning(
                "consul registration skipped, port 0 is a random port - name=%s", self.name
            )
            return

        consul = self._agent_client()
        address = (
            None if consul is None else resolve_address(self._address, listening_host, consul.url)
        )

        _ = self.register(port=listening_port, address=address)


def _serves_requests(
    application: flask.Flask,
    *,
    debug: bool | None,
    load_dotenv: bool,
    use_reloader: object,
) -> bool:
    """Tell whether this ``Flask.run`` call serves requests itself, using Flask's own rules.

    With the reloader, the first process only watches the files and starts a second one, which
    serves: only that one registers. The reloader follows the debug mode, unless ``use_reloader``
    is given: ``debug`` wins, then ``FLASK_DEBUG`` when ``.env`` files are loaded, then
    ``application.debug``.
    """
    if is_running_from_reloader():
        return True

    if use_reloader is None:
        effective_debug = application.debug

        if get_load_dotenv(load_dotenv) and "FLASK_DEBUG" in os.environ:
            effective_debug = get_debug_flag()

        if debug is not None:
            effective_debug = debug

        use_reloader = effective_debug

    return not use_reloader


def _server_name(application: flask.Flask) -> tuple[str | None, int | None]:
    """Return the host and port of the ``SERVER_NAME`` setting, the fallback of ``Flask.run``."""
    configuration = cast("Mapping[str, object]", application.config)
    server_name = configuration.get("SERVER_NAME")
    if not isinstance(server_name, str) or not server_name:
        return None, None

    host, _, port = server_name.partition(":")

    return host or None, int(port) if port else None


def _ensure_health_route(application: flask.Flask, health_path: str) -> None:
    if not _has_route(application, health_path):
        application.add_url_rule(
            health_path,
            endpoint="consulwire_health",
            view_func=_healthy,
            methods=["GET"],
        )


def _healthy() -> flask.Response:
    return flask.Response(status=200)


def _has_route(application: flask.Flask, path: str) -> bool:
    adapter = application.url_map.bind("localhost")

    try:
        _ = adapter.match(path, method="GET")
    except HTTPException:
        return False

    return True
