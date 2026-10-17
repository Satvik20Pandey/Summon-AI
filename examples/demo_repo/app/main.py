"""Tiny demo service the Summon demo tasks run against. Disposable."""

import subprocess

from fastapi import FastAPI

GIT_COMMIT = subprocess.run(
    ["git", "rev-parse", "HEAD"], capture_output=True, text=True
).stdout.strip()[:7]

app = FastAPI(title="demo-api")

ITEMS = {"1": {"id": "1", "name": "first"}}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/version")
def version():
    return {"version": "0.1.0", "commit": GIT_COMMIT}


@app.get("/items")
def list_items():
    return list(ITEMS.values())


@app.get("/items/count")
def count_items():
    return {"count": len(ITEMS)}


@app.get("/items/{item_id}")
def get_item(item_id: str):
    return ITEMS.get(item_id) or {"error": "not found"}
