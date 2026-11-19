import os
from contextlib import contextmanager

import httpx
from fastapi import HTTPException


class Tracker:
    def __init__(self, client):
        self.client = client

    def request(self, method, path, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else None
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status == 404:
                raise HTTPException(404, "Объект не найден в Keitaro.") from error
            raise HTTPException(502, f"Keitaro вернул HTTP {status} ({method} {path}).") from error
        except (httpx.RequestError, ValueError) as error:
            raise HTTPException(502, f"Нет корректного ответа Keitaro ({method} {path}).") from error

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)


@contextmanager
def tracker():
    url = os.getenv("KEITARO_URL", "").rstrip("/")
    key = os.getenv("KEITARO_API_KEY")
    if not url.startswith(("https://", "http://")) or not key:
        raise HTTPException(503, "Укажите KEITARO_URL и KEITARO_API_KEY в .env.")
    with httpx.Client(base_url=f"{url}/admin_api/v1/", headers={"Api-Key": key}, timeout=15) as client:
        yield Tracker(client)
