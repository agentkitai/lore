"""Server-side embedding service — singleton ONNX MiniLM-L6-v2.

Loaded once at server startup (or first call), cached in memory.
Used by both REST API writes and search.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import List, Optional
from urllib.request import Request, urlopen

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_DIR_DEFAULT = os.path.join(os.path.expanduser("~"), ".lore", "models")
_MODEL_NAME = "all-MiniLM-L6-v2"
_EMBEDDING_DIM = 384

# HuggingFace ONNX model files
_HF_BASE = "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/onnx"
_MODEL_FILES = {
    "model.onnx": f"{_HF_BASE}/model.onnx",
}

_HF_REPO = "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main"
_TOKENIZER_FILES = {
    "tokenizer.json": f"{_HF_REPO}/tokenizer.json",
    "tokenizer_config.json": f"{_HF_REPO}/tokenizer_config.json",
    "special_tokens_map.json": f"{_HF_REPO}/special_tokens_map.json",
}


def _download_file(url: str, dest: str, desc: str) -> None:
    """Download a file with progress indication."""
    req = Request(url, headers={"User-Agent": "lore/0.4"})
    response = urlopen(req, timeout=60)  # noqa: S310
    total = response.headers.get("Content-Length")
    total_bytes = int(total) if total else None

    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(".tmp")

    downloaded = 0
    chunk_size = 128 * 1024

    try:
        with open(tmp_path, "wb") as f:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)

                if total_bytes and sys.stderr.isatty():
                    pct = downloaded * 100 // total_bytes
                    mb_done = downloaded / (1024 * 1024)
                    mb_total = total_bytes / (1024 * 1024)
                    sys.stderr.write(
                        f"\r  {desc}: {mb_done:.1f}/{mb_total:.1f} MB ({pct}%)"
                    )
                    sys.stderr.flush()

        if sys.stderr.isatty() and total_bytes:
            sys.stderr.write("\n")

        tmp_path.rename(dest_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _ensure_model(model_dir: Optional[str] = None) -> str:
    """Ensure model files exist, downloading if needed. Returns model directory."""
    base = model_dir or os.environ.get("LORE_MODEL_DIR", _MODEL_DIR_DEFAULT)
    model_path = os.path.join(base, _MODEL_NAME)

    onnx_path = os.path.join(model_path, "model.onnx")
    if os.path.exists(onnx_path):
        return model_path

    logger.info("Downloading embedding model (%s)...", _MODEL_NAME)

    all_files = {**_MODEL_FILES, **_TOKENIZER_FILES}
    for filename, url in all_files.items():
        dest = os.path.join(model_path, filename)
        if not os.path.exists(dest):
            _download_file(url, dest, filename)

    logger.info("Embedding model ready.")
    return model_path


def _mean_pooling(
    token_embeddings: np.ndarray, attention_mask: np.ndarray
) -> np.ndarray:
    """Mean pooling — average token embeddings weighted by attention mask."""
    mask_expanded = np.expand_dims(attention_mask, axis=-1).astype(
        token_embeddings.dtype
    )
    summed = np.sum(token_embeddings * mask_expanded, axis=1)
    counts = np.clip(np.sum(mask_expanded, axis=1), a_min=1e-9, a_max=None)
    return summed / counts


def _normalize(embeddings: np.ndarray) -> np.ndarray:
    """L2-normalize embeddings."""
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.clip(norms, a_min=1e-9, a_max=None)
    return embeddings / norms


class ServerEmbedder:
    """Singleton server-side embedding service using ONNX MiniLM-L6-v2.

    Graceful fallback: if model fails to load, embed() returns None
    and memories are stored without embeddings.
    """

    _instance: Optional["ServerEmbedder"] = None

    def __init__(self) -> None:
        self._session = None
        self._tokenizer = None
        self._loaded = False
        self._failed = False

    @classmethod
    def get_instance(cls) -> "ServerEmbedder":
        """Return the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def load(self) -> bool:
        """Load the model. Returns True if successful."""
        if self._loaded:
            return True
        if self._failed:
            return False

        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            model_path = _ensure_model()

            self._session = ort.InferenceSession(
                os.path.join(model_path, "model.onnx"),
                providers=["CPUExecutionProvider"],
            )
            self._tokenizer = Tokenizer.from_file(
                os.path.join(model_path, "tokenizer.json")
            )
            self._tokenizer.enable_truncation(max_length=256)
            self._tokenizer.enable_padding(length=256)
            self._loaded = True
            logger.info("Embedding model loaded successfully")
            return True
        except Exception:
            self._failed = True
            logger.warning("Failed to load embedding model — memories will be stored without embeddings", exc_info=True)
            return False

    def embed(self, text: str) -> Optional[List[float]]:
        """Embed a single text string. Returns None if model not loaded."""
        if not self._loaded and not self.load():
            return None

        try:
            assert self._tokenizer is not None
            assert self._session is not None

            encoding = self._tokenizer.encode(text)
            input_ids = np.array([encoding.ids], dtype=np.int64)
            attention_mask = np.array([encoding.attention_mask], dtype=np.int64)
            token_type_ids = np.zeros_like(input_ids)

            outputs = self._session.run(
                None,
                {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                    "token_type_ids": token_type_ids,
                },
            )

            token_embeddings = outputs[0]
            pooled = _mean_pooling(token_embeddings, attention_mask)
            normalized = _normalize(pooled)
            return normalized[0].tolist()
        except Exception:
            logger.warning("Embedding failed for text", exc_info=True)
            return None
