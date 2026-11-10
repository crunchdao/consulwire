"""FastAPI: the lifespan registers the application at startup, and deregisters it at shutdown.

From this directory, with an agent on localhost:

    pip install "consulwire[fastapi]" fastapi uvicorn
    CONSUL_HOST=localhost python main.py
"""

from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI

from consulwire.integrations.fastapi import ConsulLifespan

PORT = 8000

application = FastAPI(lifespan=ConsulLifespan(name="fastapi-example", port=PORT))


@application.get("/hello")
def hello() -> str:
    return "hello"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    uvicorn.run(application, host="0.0.0.0", port=PORT)
