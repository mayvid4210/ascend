"""Trusted teaching sample with a fixed loopback address and port."""

import os

import uvicorn
from fastapi import FastAPI

app = FastAPI()
storage_path = os.environ.get("STORAGE_PATH", "./data")


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=8001)
