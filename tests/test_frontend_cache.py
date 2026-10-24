"""La interfaz web se sirve con caché correcta: el HTML se revalida y los archivos con hash se cachean.

Sin esto, el navegador embebido de la app de escritorio (que guarda su caché entre arranques) servía un index.html de hace horas,
apuntando a archivos que ya no existían: panel en negro y funciones nuevas que no aparecían.
"""

import os

import pytest
from fastapi.testclient import TestClient

from praxeon.server.app import create_app

DIST = os.path.join(os.path.dirname(__file__), "..", "web", "dist")
STATIC = os.path.join(os.path.dirname(__file__), "..", "praxeon", "server", "static")
pytestmark = pytest.mark.skipif(
    not (os.path.isfile(os.path.join(DIST, "index.html")) or os.path.isfile(os.path.join(STATIC, "index.html"))),
    reason="la interfaz web no está construida",
)


def _asset_url(client):
    html = client.get("/").text
    start = html.index("/assets/")
    return html[start: html.index('"', start)]


def test_html_is_revalidated_and_hashed_assets_are_cached():
    client = TestClient(create_app())
    for path in ("/", "/index.html"):
        assert client.get(path).headers["cache-control"] == "no-cache"
    asset = client.get(_asset_url(client))
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_conditional_request_still_answers_304_with_the_same_policy():
    client = TestClient(create_app())
    first = client.get("/")
    again = client.get("/", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.headers.get("cache-control") == "no-cache"


def test_desktop_window_url_is_different_on_every_launch():
    from praxeon.desktop.app import LocalServer

    kwargs = dict(server=None, thread=None, host="127.0.0.1", port=1234, token="t0k")
    first, second = LocalServer(**kwargs), LocalServer(**kwargs, build="otro")
    assert "?v=" in first.window_url and first.window_url.endswith("#token=t0k")
    assert first.window_url != second.window_url
