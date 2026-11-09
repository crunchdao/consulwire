# consulwire

A Python client for the [Consul](https://developer.hashicorp.com/consul) agent, and integrations that register Flask, FastAPI and gunicorn applications in it. It ships a synchronous and an asynchronous client with the same API, fully typed.

- [consulwire](#consulwire)
- [Installation](#installation)
- [Client](#client)
	- [Configuration](#configuration)
	- [Usage](#usage)
		- [Register a Service](#register-a-service)
		- [Health Checks](#health-checks)
		- [Report a TTL Check](#report-a-ttl-check)
		- [Deregister a Service](#deregister-a-service)
	- [Key/Value Store](#keyvalue-store)
		- [Read and Write](#read-and-write)
		- [Check-and-Set](#check-and-set)
		- [List Keys](#list-keys)
		- [Watch Changes](#watch-changes)
	- [Find a Service](#find-a-service)
		- [Healthy Instances](#healthy-instances)
		- [Every Instance](#every-instance)
		- [Call Another Service](#call-another-service)
	- [Locks](#locks)
		- [Hold a Lock](#hold-a-lock)
		- [When a Lock Is Lost](#when-a-lock-is-lost)
		- [Sessions](#sessions)
	- [Asynchronous Client](#asynchronous-client)
	- [Errors](#errors)
	- [Testing](#testing)
- [Integrations](#integrations)
	- [What Every Integration Does](#what-every-integration-does)
		- [The Address](#the-address)
		- [The Health Route](#the-health-route)
		- [The Access Logs](#the-access-logs)
		- [Deregistration](#deregistration)
	- [Flask](#flask)
		- [Setup](#setup)
		- [The Reloader](#the-reloader)
		- [Other Servers](#other-servers)
		- [Options](#options)
	- [FastAPI](#fastapi)
		- [Setup](#setup-1)
		- [With a Lifespan of your Own](#with-a-lifespan-of-your-own)
		- [Reload and Workers](#reload-and-workers)
		- [Options](#options-1)
	- [gunicorn](#gunicorn)
		- [Setup](#setup-2)
		- [Workers](#workers)
		- [Binary Upgrades](#binary-upgrades)
		- [Options](#options-2)

# Installation

```bash
pip install consulwire

# with the integration of your framework
pip install "consulwire[flask]"
pip install "consulwire[fastapi]"
```

Python 3.10 or newer is required. The client depends on `httpx` and `anyio`. The gunicorn integration needs no extra.

# Client

## Configuration

```python
from consulwire import Consul

# the agent is found in the environment, http://localhost:8500 otherwise
with Consul() as consul:
    ...
```

```python
# given explicitly
consul = Consul("http://consul:8500", token="a0b1c2d3e4f5g6h7i8j9", timeout=5.0)
```

The environment variables of the official tools are read first, then the ones of Spring Cloud Consul, so a Python service is configured like the Spring Cloud Consul applications next to it:

| Variable            | Meaning                                                      |
| ------------------- | ------------------------------------------------------------ |
| `CONSUL_HTTP_ADDR`  | Agent address, like `consul:8500` or `https://consul:8501`.  |
| `CONSUL_HTTP_SSL`   | `true` to use `https` when `CONSUL_HTTP_ADDR` has no scheme. |
| `CONSUL_HTTP_TOKEN` | ACL token, only needed when the agent enforces ACLs.         |
| `CONSUL_HOST`       | Agent host, when `CONSUL_HTTP_ADDR` is not set.              |
| `CONSUL_PORT`       | Agent port with `CONSUL_HOST`, `8500` by default.            |

> [!TIP]
> The client is thread-safe and holds a connection pool: create it once and reuse it.

## Usage

The client is organized like the [Consul HTTP API](https://developer.hashicorp.com/consul/api-docs): each section of the API is an attribute of the client:

| Section          | Meaning                                                                          |
| ---------------- | -------------------------------------------------------------------------------- |
| `consul.agent`   | The services of this machine: [register](#register-a-service) and deregister.    |
| `consul.kv`      | The [key/value store](#keyvalue-store).                                          |
| `consul.health`  | The [healthy instances](#healthy-instances) of a service, the ones to call.     |
| `consul.catalog` | [Every instance](#every-instance) of every service, healthy or not.              |
| `consul.session` | [Sessions](#sessions), which lock keys of the store: see [`consul.lock`](#locks). |

### Register a Service

```python
consul.agent.register_service("my-service", address="my-service", port=8000)
```

```python
# every option
consul.agent.register_service(
    "my-service",
    address="10.0.0.5",
    port=8000,
    service_id="my-service-1",
    tags=["internal"],
    meta={"version": "1.2.0"},
    checks=[...],
    replace_existing_checks=True,
)
```

> [!NOTE]
> The instance ID is the service name unless `service_id` is given: registering the same name again replaces the instance instead of adding one. <br />
> Without `address`, Consul uses the agent's own address, which is wrong for a service in another container.

### Health Checks

A check is a plain dictionary: nothing to import. Type checkers still verify its keys and values.

```python
from datetime import timedelta

consul.agent.register_service(
    "my-service",
    address="my-service",
    port=8000,
    checks=[
        # healthy while the URL answers a 2xx status
        {"http": "http://my-service:8000/health", "interval": timedelta(seconds=10)},
        # healthy while a TCP connection can be opened
        {"tcp": "my-service:8000", "interval": timedelta(seconds=30)},
        # healthy while the service reports so, see below
        {"ttl": timedelta(seconds=30), "id": "my-service-alive"},
    ],
)
```

| Key                         | Checks    | Meaning                                                         |
| --------------------------- | --------- | --------------------------------------------------------------- |
| `http`                      | HTTP      | URL the agent requests, as reachable from the agent.            |
| `tcp`                       | TCP       | `host:port` the agent connects to, as reachable from the agent. |
| `ttl`                       | TTL       | Time after the last report before the check turns critical.     |
| `interval`                  | HTTP, TCP | Time between two checks. Required.                              |
| `timeout`                   | HTTP, TCP | Time before a check counts as failed, 10 seconds by default.    |
| `method`                    | HTTP      | HTTP method, `GET` by default.                                  |
| `headers`                   | HTTP      | Request headers, such as `{"Authorization": "..."}`.            |
| `tls_skip_verify`           | HTTP      | Accept any TLS certificate.                                     |
| `deregister_critical_after` | all       | Remove the service once the check has been failing this long.   |
| `id`                        | all       | Check ID, `service:<service id>` by default.                    |
| `name`                      | all       | Check name shown in the Consul UI.                              |

> [!NOTE]
> Consul counts a `429 Too Many Requests` answer as a warning, and any other non-2xx answer as critical. <br />
> It removes services through `deregister_critical_after` every 30 seconds, and ignores values under a minute.

To annotate a variable, the types are in `consulwire.agent`:

```python
from consulwire.agent import Check, HTTPCheck, TCPCheck, TTLCheck
```

### Report a TTL Check

With a TTL check, Consul does not call the service: the service reports its own health, more often than the `ttl`. It fits a process without an HTTP server, like a queue consumer, and reports from inside the work itself, so a stuck loop shows as unhealthy.

```python
while True:
    consume_messages()

    consul.agent.pass_check("my-service-alive", note="42 messages consumed")
```

| Call                             | Status   | Healthy |
| -------------------------------- | -------- | ------- |
| `consul.agent.pass_check(id)`    | passing  | yes     |
| `consul.agent.warn_check(id)`    | warning  | no      |
| `consul.agent.fail_check(id)`    | critical | no      |
| no report for `ttl`              | critical | no      |

The `note` is shown with the status in the Consul UI.

> [!NOTE]
> A TTL check starts critical: the instance is not healthy until its first report. <br />
> Without an `id`, the check is named `service:<service id>`, followed by `:1`, `:2`... when the service has several checks: an `id` of your own is easier to report to.

### Deregister a Service

```python
consul.agent.deregister_service("my-service")
```

> [!NOTE]
> The argument is the instance ID, which is the service name unless another `service_id` was registered. <br />
> An unknown ID raises `HTTPStatusError` with the status `404`.

## Key/Value Store

`consul.kv` reads and writes [Consul's key/value store](https://developer.hashicorp.com/consul/api-docs/kv). Values are raw bytes: the application decides their format, whether text, JSON or YAML.

### Read and Write

```python
consul.kv.put("my-service/feature-flag", "on")  # text is stored in UTF-8

flag = consul.kv.get("my-service/feature-flag")  # b"on", or None when the key does not exist
enabled = flag == b"on"

consul.kv.delete("my-service/feature-flag")  # deleting a missing key succeeds
consul.kv.delete_prefix("my-service/")  # every key starting with the prefix
```

`get_entry` returns the key with its metadata, as an `Entry`:

| Attribute      | Meaning                                                              |
| -------------- | -------------------------------------------------------------------- |
| `key`          | Full key.                                                            |
| `value`        | Raw value, `b""` when the key holds none.                            |
| `flags`        | Number the application attached with `put(..., flags=)`, `0` by default. |
| `modify_index` | Index of the last write, for [check-and-set](#check-and-set).        |
| `create_index` | Index of the write that created the key.                             |
| `lock_index`   | Number of times a session acquired the key.                          |
| `session`      | Session holding the lock on the key, if any.                         |

> [!NOTE]
> Consul refuses values over 512 KB with a `413`, raised as `HTTPStatusError`. <br />
> `delete_prefix("")` raises a `ValueError` instead of deleting the whole store.

### Check-and-Set

With `cas`, a write only happens when the key did not change since it was read, so two writers cannot overwrite each other:

```python
entry = consul.kv.get_entry("my-service/counter")
count = int(entry.value)

if not consul.kv.put("my-service/counter", str(count + 1), cas=entry.modify_index):
    ...  # another writer was first: read the key again and retry
```

`cas=0` writes only when the key does not exist yet. `delete` takes `cas` too. Both return `False` when the check failed.

### List Keys

```python
consul.kv.entries("my-service/")  # every entry starting with the prefix, sorted by key
consul.kv.keys("my-service/")  # only their names

# one level, like a folder listing: ["my-service/database/", "my-service/feature-flag"]
consul.kv.keys("my-service/", separator="/")
```

A prefix without any key gives an empty list. Without a prefix, the whole store is listed.

### Watch Changes

`watch` yields a key now, then every time it changes, and `None` while it does not exist. It relies on Consul's [blocking queries](https://developer.hashicorp.com/consul/api-docs/features/blocking): a request stays open until the key changes, so a change arrives within milliseconds, without polling.

```python
for entry in consul.kv.watch("my-service/feature-flag"):
    enabled = entry is not None and entry.value == b"on"
```

`watch_entries("my-service/")` yields every entry starting with a prefix the same way, whenever one of them is created, written or deleted.

> [!IMPORTANT]
> The loop never ends by itself: run it in a thread of its own, or as a task with the asynchronous client (`async for entry in consul.kv.watch(...)`). <br />
> It stops at the first error, such as the agent restarting: whether to retry, and how long to wait first, is the application's choice.

Each request is held up to `wait`, 5 minutes by default and 10 at most, and the client waits that long on top of its timeout.

## Find a Service

### Healthy Instances

`consul.health.service` returns the instances of a service that pass every check: the ones to call.

```python
instances = consul.health.service("web")
instance = instances[0]

instance.url("/orders")  # "http://10.0.0.5:8005/orders"
```

| Attribute | Meaning                                                                               |
| --------- | ------------------------------------------------------------------------------------- |
| `id`      | Instance ID, unique on its agent.                                                     |
| `name`    | Service name.                                                                         |
| `address` | Address to connect to: the one the instance registered, or its node's otherwise.     |
| `port`    | Port to connect to.                                                                   |
| `tags`    | Service tags.                                                                         |
| `meta`    | Service key and value pairs.                                                          |
| `node`    | Name of the node the instance is registered on.                                       |

`tag="internal"` keeps the instances with that tag. An unknown service has no instance.

`watch_service` follows them through blocking queries, like the [key/value watches](#watch-changes): it yields the instances now, then every time one starts, stops, or fails its checks.

```python
for instances in consul.health.watch_service("web"):
    ...
```

> [!NOTE]
> An instance in warning, like one answering `429 Too Many Requests` to its health check, does not pass. `healthy_only=False` returns every instance, failing ones included.

### Every Instance

`consul.catalog` lists what is registered, healthy or not:

```python
consul.catalog.services()  # {"consul": [], "web": ["internal"]}
consul.catalog.service("web")  # every instance, failing ones included
```

### Call Another Service

Each call can go to the next healthy instance, so the load is spread and a failing instance leaves the rotation within a check interval. A watch keeps the list up to date in a thread, so picking an instance sends nothing to Consul.

<details>
<summary><code>RoundRobin</code>: the healthy instances, followed by a watch, picked in turn</summary>

```python
import itertools
import logging
import threading
import time

from consulwire import Consul, ConsulError
from consulwire.health import ServiceInstance

logger = logging.getLogger(__name__)


class RoundRobin:
    """The healthy instances of a service, each call picking the next one."""

    def __init__(self, consul: Consul, name: str) -> None:
        self.name = name
        self._consul = consul
        self._instances: list[ServiceInstance] = consul.health.service(name)
        self._turns = itertools.count()

        threading.Thread(target=self._follow, name=f"watch {name}", daemon=True).start()

    def pick(self) -> ServiceInstance:
        instances = self._instances  # replaced as a whole by the watch, never changed in place

        if not instances:
            message = f"no healthy instance of {self.name}"
            raise LookupError(message)

        return instances[next(self._turns) % len(instances)]

    def _follow(self) -> None:
        while True:
            try:
                for instances in self._consul.health.watch_service(self.name):
                    self._instances = instances
            except ConsulError as error:
                logger.warning("watch of %s failed, retrying in 5 seconds: %s", self.name, error)
                time.sleep(5)
```

</details>

Create it once, next to the client, and pick an instance for each call. An instance can still stop between two health checks: trying the next one covers it.

```python
import httpx

consul = Consul()
web = RoundRobin(consul, "web")


def create_order(body: dict) -> dict:
    for _ in range(3):
        instance = web.pick()

        try:
            response = httpx.post(instance.url("/orders"), json=body, timeout=60)
        except httpx.TransportError:
            continue  # unreachable: the next instance

        response.raise_for_status()
        return response.json()

    raise RuntimeError("no instance of web answered")
```

> [!TIP]
> The client must stay open as long as the rotation is used: the watch shares it. When the agent restarts, the watch fails, waits 5 seconds and starts again, while `pick` keeps the last known instances.

## Locks

### Hold a Lock

`consul.lock(key)` runs a block in one process of the cluster at a time, like a scheduled job several instances could start:

```python
with consul.lock("jobs/nightly-report"):
    send_report()
```

Entering waits as long as another process holds the key. With a `timeout`, it raises `TimeoutError` instead, and `timedelta(0)` only tries once, to skip work another instance is already doing:

```python
from datetime import timedelta

try:
    with consul.lock("jobs/nightly-report", timeout=timedelta(0)):
        send_report()
except TimeoutError:
    pass  # another instance is sending it
```

| Option       | Meaning                                                                                                  |
| ------------ | -------------------------------------------------------------------------------------------------------- |
| `value`      | Value of the key while held, like the name of the holder.                                                |
| `ttl`        | Time after the last renewal before Consul frees the lock of a process that died. 15 seconds by default, 10 at least. |
| `lock_delay` | Time the key stays unlockable after its holder's session ended, 15 seconds by default.                  |
| `timeout`    | Longest wait for the key, as long as needed by default.                                                  |

With the asynchronous client, it is `async with consul.lock(...)`.

> [!NOTE]
> A lock is a [session](#sessions) holding the key, renewed every half TTL from a thread, or a task with the asynchronous client. Leaving releases the key first, so the next process takes it at once, then ends the session.

### When a Lock Is Lost

A process that dies holds nothing for long: Consul ends its session once the TTL ran out, up to twice the TTL, then the key stays unlockable for the lock delay. With the defaults, another process takes over 30 to 45 seconds later.

The lock can also be lost while held: when the agent or the network fails for longer than the TTL, the session ends, and another process may take the key after the lock delay. The code holding it keeps running, since nothing can stop it from outside, so:

- `lock.lost` is set: check it before doing what must not happen twice;
- `lock.lock_index` grows with every holder: pass it to the systems the code writes to, so they refuse the writes of an older holder.

```python
with consul.lock("jobs/nightly-report") as lock:
    report = build_report()

    if lock.lost.is_set():
        raise RuntimeError("another instance may be sending it too")

    send(report)
```

> [!TIP]
> The lock delay is the margin: a holder notices the loss within its TTL, while no other process can take the key before the lock delay ran out.

### Sessions

Locks are built on `consul.session`, which is also available directly. A session holds keys with `acquire=` and `release=`, and they are released, or deleted with `behavior="delete"`, when it ends:

```python
# a presence list: the key of each worker disappears when the worker stops renewing
session_id = consul.session.create(ttl=timedelta(seconds=30), behavior="delete")
consul.kv.put("workers/worker-1", "alive", acquire=session_id)

consul.session.renew(session_id)  # False once the session ended
consul.session.destroy(session_id)
```

A session also ends when its node fails, or when one of its `service_checks` fails. `consul.session.info(session_id)` returns it, or `None` once it ended.

## Asynchronous Client

`AsyncConsul` has the same API, with coroutines:

```python
from consulwire import AsyncConsul

async with AsyncConsul() as consul:
    await consul.agent.register_service("my-service", address="my-service", port=8000)
```

Both clients describe their requests with the same code, so they cannot drift apart.

An application that needs both, like a FastAPI service with plain `def` endpoints, configures one and converts it, with the same agent, token and timeout:

```python
consul = Consul("http://consul:8500", token="a0b1c2d3e4f5g6h7i8j9")

async_consul = consul.as_async()  # and AsyncConsul.as_sync() the other way
```

> [!NOTE]
> The converted client is a new one, with connections of its own: closing one leaves the other open. <br />
> A client given an HTTP `transport`, as in tests, passes it on when the transport's class supports the other mode, and raises `TypeError` otherwise. A `MockTransport` whose handler is only sync or only async still fails at its first request: build the test clients of both kinds from the [in-memory agent](#testing) instead, with `agent.consul()` and `agent.async_consul()`.

## Errors

Every error raised by the package derives from `ConsulError`:

```
ConsulError
├── ConfigurationError   an environment variable holds an invalid value (also a ValueError)
├── TransportError       the agent could not be reached: connection refused, timeout, DNS failure
├── HTTPStatusError      the agent answered with an error status (.status_code, .body)
└── ResponseError        the agent answered a body without the shape of its API
```

```python
from consulwire import ConsulError, HTTPStatusError

try:
    consul.agent.register_service("my-service", port=8000, checks=[...])
except HTTPStatusError as error:
    # Consul explains its refusals in plain text, like "Invalid check: Interval must be > 0"
    print("Refused: %s" % error.body)
except ConsulError as error:
    print("Consul is unavailable: %s" % error)
```

## Testing

`consulwire.testing.InMemoryAgent` answers both clients from memory, the way an agent would: service registration and TTL checks, the catalog and health lookups, the key/value store with check-and-set, and sessions with their locks, all with blocking queries. An application and its integration are tested without Consul:

```python
from consulwire.testing import InMemoryAgent

agent = InMemoryAgent()
lifespan = ConsulLifespan(name="my-service", port=8000, consul=agent.async_consul())

with TestClient(FastAPI(lifespan=lifespan)):
    assert agent.services["my-service"]["Port"] == 8000

assert agent.services == {}  # deregistered at shutdown
```

| Member                                         | Meaning                                                                        |
| ---------------------------------------------- | ------------------------------------------------------------------------------ |
| `agent.consul()`, `agent.async_consul()`       | Clients of the agent.                                                          |
| `agent.transport()`, `agent.async_transport()` | Its HTTP transports, for a client the application creates: `Consul(transport=agent.transport())`. |
| `agent.services`                               | Registered services by instance ID, with Consul's field names: `Name`, `Port`, `Checks`. |
| `agent.kv`                                     | A copy of the key/value store.                                                 |
| `agent.requests`                               | Every request received, as `httpx.Request`.                                    |
| `agent.set_passing(service_id, passing=False)` | Make an instance fail its checks, to test a caller: it leaves `health.service`. |

Instances pass their checks until told otherwise, except the ones with a TTL check, which start critical until they report. Sessions lock keys like in Consul. Time does not pass in the agent: TTLs never run out and keys have no lock delay, so a test ends a session to lose its lock, with `consul.session.destroy(lock.session_id)`. Other endpoints answer `404`.

# Integrations

Each framework or server has its own module in `consulwire.integrations`. The framework ones need the matching extra:

| Framework                      | Module                             | Extra                 | Entry point       |
| ------------------------------ | ---------------------------------- | --------------------- | ----------------- |
| Flask                          | `consulwire.integrations.flask`    | `consulwire[flask]`   | `ConsulExtension` |
| FastAPI, and any Starlette app | `consulwire.integrations.fastapi`  | `consulwire[fastapi]` | `ConsulLifespan`  |
| gunicorn, with any framework   | `consulwire.integrations.gunicorn` | none                  | `ConsulHooks`     |

## What Every Integration Does

### The Address

Other services must reach the application at the registered address. It is chosen in this order:

1. the `address` given to the integration;
2. the host the server listens on, when it names this machine for others (not `0.0.0.0`, `::`, `127.0.0.1` or `localhost`);
3. the local IP address the system uses to reach the agent: the container IP in a Docker network, the LAN IP when the agent is on another machine.

> [!NOTE]
> When the agent is reached through a loopback address, such as an agent in Docker reached through a published port, its own loopback is not this process. The address of the default route is used instead, IPv4 first, like Spring Cloud Consul does.

### The Health Route

Consul checks the application at `health_path`, `/health` by default:

- when the application already has a `GET` route there, it is used as it is, so a real check, like a database ping, stays in charge;
- otherwise, a route answering `200` is added right before the registration, once every route of the application is declared. FastAPI leaves it out of the OpenAPI schema.

Under gunicorn, the Flask and FastAPI integrations still add it, while [the hooks](#gunicorn) register the application.

### The Access Logs

Consul calls the health route every 10 seconds. Those calls are hidden from the access logs of the servers, so the logs keep the real traffic:

| Integration | Access loggers                      |
| ----------- | ----------------------------------- |
| Flask       | `werkzeug`, `gunicorn.access`       |
| FastAPI     | `uvicorn.access`, `gunicorn.access` |
| gunicorn    | `gunicorn.access`, `uvicorn.access` |

Only the `GET` requests of the health route are hidden. `log_health_checks=True` keeps them.

### Deregistration

An application is deregistered only when its framework has a shutdown event:

| Integration | Deregistered                       |
| ----------- | ---------------------------------- |
| Flask       | never: Flask has no shutdown event |
| FastAPI     | at the shutdown of the lifespan    |
| gunicorn    | when gunicorn exits                |

An instance that stopped without deregistering, after a crash or a `kill -9`, is removed by Consul once its health check has been failing for `deregister_critical_after`, a minute by default, so about a minute and a half with Consul's reaping cycle. Restarting first replaces it: the instance ID is the service name.

When no agent is configured, neither through the `consul` argument nor the environment, registration is skipped with a log line, and the health route is still added. The same code runs on a laptop without Consul.

## Flask

### Setup

```python
import flask

from consulwire.integrations.flask import ConsulExtension

application = flask.Flask(__name__)
ConsulExtension(application, name="my-service")


@application.get("/hello")
def hello() -> str:
    return "hello"


application.run(host="0.0.0.0", port=8000)
```

`application.run()` registers the application right before it listens, with the port and host it is about to use, read the way Flask reads them: its arguments, then the `SERVER_NAME` setting, then Flask's default port, `5000`.

```python
# with the application factory pattern
consul_extension = ConsulExtension(name="my-service")


def create_application() -> flask.Flask:
    application = flask.Flask(__name__)
    consul_extension.init_app(application)

    return application
```

> [!NOTE]
> A random port (`port=0`) is not registered, since the application cannot know which one the system picked.

### The Reloader

With the reloader (`debug=True`, `FLASK_DEBUG=1` or `use_reloader=True`), Flask starts two processes: one watching the files, and one serving requests, restarted on every change. Only the serving process registers, and each restart replaces the instance.

`.env` files are loaded before registering, the way `application.run()` loads them, so `CONSUL_HOST` and `FLASK_DEBUG` can come from them.

> [!WARNING]
> The reloader and the debugger are for development only. The debugger runs any code sent to it: never start a production service with `debug=True`.

### Other Servers

Under gunicorn, the [gunicorn hooks](#gunicorn) register the application, and the extension adds the health route right before the first request.

Other servers that do not go through `application.run()`, like uWSGI, Waitress or `flask run`, register the application themselves:

```python
consul_extension = ConsulExtension(application, name="my-service")
consul_extension.register(port=int(os.environ["PORT"]))
```

> [!TIP]
> Every process registering the same instance is harmless: the instance ID is the service name.

`consul_extension.deregister()` is available to an application with a shutdown event of its own. Like every integration at shutdown, it logs its errors instead of raising them.

### Options

```python
ConsulExtension(
    application,
    name="my-service",  # name other services look this one up by
    health_path="/health",  # route Consul checks
    address=None,  # see The Address
    consul=None,  # Consul client, from the environment by default
    check_interval=timedelta(seconds=10),  # time between two health checks
    deregister_critical_after=timedelta(minutes=1),  # None keeps dead instances
    tags=(),
    meta=None,
    log_health_checks=False,  # True keeps them in the access logs
)
```

The extension is stored in `application.extensions["consul"]`, and `ConsulExtension.of(application)` returns it.

## FastAPI

### Setup

```python
import uvicorn
from fastapi import FastAPI

from consulwire.integrations.fastapi import ConsulLifespan

application = FastAPI(lifespan=ConsulLifespan(name="my-service", port=8000))


@application.get("/hello")
def hello() -> str:
    return "hello"


uvicorn.run(application, host="0.0.0.0", port=8000)
```

The lifespan registers the application at startup, before the first request, and deregisters it at shutdown. It uses the asynchronous client, and works with any Starlette application.

> [!IMPORTANT]
> The `port` must match the one given to the server: uvicorn and the other ASGI servers do not tell it to the application. Under gunicorn, [the hooks](#gunicorn) find it themselves.

### With a Lifespan of your Own

FastAPI takes a single lifespan: call the Consul one from yours.

```python
from contextlib import asynccontextmanager

consul_lifespan = ConsulLifespan(name="my-service", port=8000)


@asynccontextmanager
async def lifespan(application: FastAPI):
    database = await connect()

    async with consul_lifespan(application):
        yield

    await database.close()


application = FastAPI(lifespan=lifespan)
```

Registering last and deregistering first, as above, keeps the instance out of Consul while its dependencies are not ready.

> [!WARNING]
> Once a lifespan is given, Starlette stops calling the `@application.on_event("startup")` and `"shutdown"` handlers, which some libraries still use. Move them into the lifespan.

### Reload and Workers

- `uvicorn --reload` restarts the application on every change: each restart is a shutdown then a startup, so the instance is deregistered then registered again.
- With `uvicorn --workers 4`, every worker registers the same instance, which is harmless, and deregisters it when it stops: a worker restarting removes the instance until it registers again.
- Under gunicorn, the [gunicorn hooks](#gunicorn) register the application once, from the main process: the lifespan leaves the registration to them, so workers restarting do not touch Consul.

### Options

```python
ConsulLifespan(
    name="my-service",  # name other services look this one up by
    port=8000,  # port given to the server
    health_path="/health",  # route Consul checks
    address=None,  # see The Address
    consul=None,  # AsyncConsul client, from the environment by default
    check_interval=timedelta(seconds=10),  # time between two health checks
    deregister_critical_after=timedelta(minutes=1),  # None keeps dead instances
    tags=(),
    meta=None,
    log_health_checks=False,  # True keeps them in the access logs
)
```

## gunicorn

### Setup

gunicorn calls the hooks of its configuration file. The ones of `consulwire.integrations.gunicorn` register the application from gunicorn's main process, whatever its framework:

```python
# gunicorn.conf.py
from consulwire.integrations.gunicorn import ConsulHooks

bind = "0.0.0.0:8000"
workers = 4

consul_hooks = ConsulHooks(name="my-service")

when_ready = consul_hooks.when_ready
on_exit = consul_hooks.on_exit
```

- `when_ready` runs once gunicorn listens, before the workers start: it registers the application with the port gunicorn is bound to, found automatically.
- `on_exit` deregisters it when gunicorn stops.

With hooks of your own, call these from yours, like `consul_hooks.when_ready(server)`.

> [!NOTE]
> The application must answer the health route: the Flask and FastAPI integrations add it when it is missing. With another framework, declare it.

### Workers

Only the main process registers, so workers restarting, after a crash, with `--max-requests` or with `--reload`, do not touch Consul.

The integrations running in the workers leave the registration to the hooks, and still add the health route and hide the health checks:

- FastAPI: `ConsulLifespan` neither registers nor deregisters;
- Flask: `ConsulExtension` adds the health route right before the first request.

The hooks tell them through the `CONSULWIRE_REGISTERED_BY` environment variable, set before the workers start.

### Binary Upgrades

During a binary upgrade (`kill -USR2`), the new main process registers the same instance: the old one exits without deregistering it.

### Options

```python
ConsulHooks(
    name="my-service",  # name other services look this one up by
    port=None,  # port other services connect to, the first TCP port gunicorn listens on by default
    health_path="/health",  # route Consul checks, answered by the application
    address=None,  # see The Address
    consul=None,  # Consul client, from the environment by default
    check_interval=timedelta(seconds=10),  # time between two health checks
    deregister_critical_after=timedelta(minutes=1),  # None keeps dead instances
    tags=(),
    meta=None,
    log_health_checks=False,  # True keeps them in the access logs
)
```

When gunicorn only listens on a UNIX socket, like behind nginx, give the `port` and `address` other services reach it at: otherwise the registration is skipped with a warning.
