"""URLs of the Django project the tests set up."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.http import HttpResponse
from django.urls import path

if TYPE_CHECKING:
    from django.http import HttpRequest


def health(_request: HttpRequest) -> HttpResponse:
    return HttpResponse()


urlpatterns = [path("health", health)]
