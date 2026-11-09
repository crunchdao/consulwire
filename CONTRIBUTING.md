# Contributing

## Development

```bash
pip install -e . --group dev               # pip >= 25.1

ruff format --check . && ruff check .     # every rule enabled
mypy                                      # strict + extra error codes, Python 3.10 target
basedpyright                              # typeCheckingMode = "all", Python 3.10 target
pytest --cov                              # offline: in-memory fake agent, 100% coverage
```

To try an integration against a real agent:

```bash
docker run -d --rm --name consul -p 127.0.0.1:8500:8500 hashicorp/consul agent -dev -client 0.0.0.0
CONSUL_HTTP_ADDR=127.0.0.1:8500 python my_application.py
```

The registered services and their health checks show up at <http://127.0.0.1:8500/ui>.

The CI (`.github/workflows/ci.yml`) runs the same checks on every push to `master` and every pull request, with the tests on Python 3.10 to 3.14.

## Adding a Section of the API

Each section of the [agent HTTP API](https://developer.hashicorp.com/consul/api-docs) is a package, like `consulwire/agent/`:

- `_requests.py` describes the requests as `Request` values, written once for both clients;
- `__init__.py` holds the section class of each client, such as `Agent` and `AsyncAgent`, sending those requests through the transport of their client;
- `_client.py` creates the section as an attribute of `Consul` and `AsyncConsul`.

## Releasing

1. Bump `__version__` in `src/consulwire/_version.py` and merge it into `master`.
2. Publish a GitHub release whose tag is that exact version, such as `0.2.0`.

`.github/workflows/release.yml` then runs the CI again, checks that the tag matches the version, builds the sdist and wheel, publishes them to PyPI, and attaches them to the GitHub release.

Publishing uses PyPI [trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API token is stored. It needs a one-time setup:

- on PyPI, add a trusted publisher for this repository, workflow `release.yml`, environment `pypi` (a "pending publisher" for the first release);
- on GitHub, create the `pypi` environment, optionally restricted to release tags or protected by required reviewers.
