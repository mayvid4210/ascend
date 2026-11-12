"""Trusted teaching sample. Loopback binding and local storage are intentional."""

import os
from pathlib import Path

from flask import Flask

app = Flask(__name__)
storage = Path("./uploads")
storage.mkdir(exist_ok=True)
app.config["UPLOAD_FOLDER"] = str(storage)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
