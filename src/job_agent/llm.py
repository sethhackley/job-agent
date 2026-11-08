"""Thin client for the local Ollama server (chat + embeddings)."""
from __future__ import annotations

import json

import httpx
import numpy as np


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, settings: dict):
        self.url = settings["ollama_url"].rstrip("/")
        self.chat_model = settings["chat_model"]
        self.check_model = settings.get("check_model") or self.chat_model
        self.embed_model = settings["embed_model"]
        self.num_ctx = int(settings.get("num_ctx", 16384))
        self.http = httpx.Client(timeout=httpx.Timeout(600, connect=10))

    def ping(self) -> str:
        try:
            r = self.http.get(f"{self.url}/api/tags")
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Can't reach Ollama at {self.url}: {e}") from e
        names = {m["name"] for m in r.json().get("models", [])}
        missing = [m for m in {self.chat_model, self.check_model, self.embed_model}
                   if m not in names and f"{m}:latest" not in names]
        if missing:
            raise LLMError(f"Ollama is running but these models aren't pulled: {', '.join(missing)}")
        return f"Connected. Models: {', '.join(sorted(names))}"

    def chat(self, system: str, user: str, schema: dict | None = None,
             temperature: float = 0.4, model: str | None = None) -> str | dict:
        body = {
            "model": model or self.chat_model,
            "stream": False,
            "think": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "options": {"temperature": temperature, "num_ctx": self.num_ctx},
        }
        if schema:
            body["format"] = schema
        try:
            r = self.http.post(f"{self.url}/api/chat", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Chat request failed: {e}") from e
        text = r.json()["message"]["content"].strip()
        if schema:
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                raise LLMError(f"Model returned invalid JSON: {text[:200]}") from e
        return text

    def embed(self, texts: list[str], kind: str = "document", batch: int = 64) -> np.ndarray:
        """Unit-normalised embeddings. nomic-embed-text expects task prefixes."""
        prefix = "search_query: " if kind == "query" else "search_document: "
        if "nomic" not in self.embed_model:
            prefix = ""
        out = []
        for i in range(0, len(texts), batch):
            chunk = [prefix + t for t in texts[i:i + batch]]
            try:
                r = self.http.post(f"{self.url}/api/embed",
                                   json={"model": self.embed_model, "input": chunk, "truncate": True})
                r.raise_for_status()
            except httpx.HTTPError as e:
                raise LLMError(f"Embedding request failed: {e}") from e
            out.extend(r.json()["embeddings"])
        arr = np.asarray(out, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / np.clip(norms, 1e-9, None)
