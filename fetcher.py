from __future__ import annotations

import json
import os
import threading
import time

import requests

BASE_URL = "https://www.alquilerseguro.es/api/inmueble/get-all"
LANG = "es-ES"

CACHE_TTL = float(os.environ.get("CACHE_TTL", "120"))

_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()


def _cache_key(provincia: str | None) -> str:
    return (provincia or "").strip().upper()


def fetch(provincia: str | None) -> list[dict]:
    key = _cache_key(provincia)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and now - cached[0] < CACHE_TTL:
            return cached[1]

    params = {"lang": LANG}
    if provincia:
        params["provincia"] = provincia.strip()
    resp = requests.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = json.loads(resp.text, strict=False).get("data", [])

    with _cache_lock:
        _cache[key] = (time.monotonic(), data)
    return data


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def matches(prop: dict, config: dict) -> bool:
    precio = _num(prop.get("precio"))
    precio_min = _num(config.get("precio_min"))
    precio_max = _num(config.get("precio_max"))
    if precio is not None:
        if precio_min is not None and precio < precio_min:
            return False
        if precio_max is not None and precio > precio_max:
            return False

    habitaciones = config.get("habitaciones")
    if habitaciones is not None:
        if prop.get("habitaciones") is None or prop["habitaciones"] < habitaciones:
            return False

    banyos = config.get("banyos")
    if banyos is not None:
        if prop.get("banyos") is None or prop["banyos"] < banyos:
            return False

    return True


def filter_props(properties: list[dict], config: dict) -> list[dict]:
    return [p for p in properties if matches(p, config)]


def search(config: dict) -> list[dict]:
    return filter_props(fetch(config.get("provincia")), config)
