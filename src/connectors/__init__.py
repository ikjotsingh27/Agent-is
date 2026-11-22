"""Finds the connectors. Each connector is one folder here with a guide.json and a connector.py.

Adding a connector means adding a folder: nothing else in the app has to change.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

from .base import CheckResult, Connector

HERE = Path(__file__).resolve().parent
ORDER = ["telegram", "gmail", "calendar", "discord", "web"]  # the build order from the blueprint


def load_connectors(**options) -> dict:
    """{id: Connector} for every folder that has a guide.json, in build order.

    Options (like a fake network for tests) are passed to each connector's create().
    """
    found = {}
    for folder in sorted(p for p in HERE.iterdir() if (p / "guide.json").exists()):
        guide = json.loads((folder / "guide.json").read_text(encoding="utf-8"))
        module = importlib.import_module(f"{__name__}.{folder.name}.connector")
        found[guide["id"]] = module.create(guide, **options)
    return dict(sorted(found.items(), key=lambda kv: ORDER.index(kv[0]) if kv[0] in ORDER else len(ORDER)))


__all__ = ["load_connectors", "CheckResult", "Connector"]
