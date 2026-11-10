"""Routes of the project: Django does not add the health route, the project declares it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.http import HttpResponse
from django.urls import path

if TYPE_CHECKING:
    from django.http import HttpRequest


def health(_request: HttpRequest) -> HttpResponse:
    return HttpResponse()


def hello(_request: HttpRequest) -> HttpResponse:
    return HttpResponse("hello")


urlpatterns = [
    path("health", health),
    path("hello", hello),
]
