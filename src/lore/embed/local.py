"""Local embedding engine using ONNX MiniLM-L6-v2."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from urllib.request import Request, urlopen

import numpy as np

from lore.embed.base import Embedder

logger = logging.getLogger(__name__)

_MODEL_DIR = os.path.join(os.path.expanduser("~"), ".lore", "models")

_EMBEDDING_DIM = 384


# ---------------------------------------------------------------------------
# Model registry — each entry describes how to download one ONNX model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ModelSpec:
    name: str
    hf_repo: str
    onnx_subdir: str  # subdir under the repo for model.onnx ("onnx" or "")
    dim: int = _EMBEDDING_DIM


PROSE_MODEL = _ModelSpec(
    name="all-MiniLM-L6-v2",
    hf_repo="sentence-transformers/all-MiniLM-L6-v2",
    onnx_subdir="onnx",
)

CODE_MODEL = _ModelSpec(
    name="all-MiniLM-L6-v2-code",
    hf_repo="flax-sentence-embeddings/st-codesearch-distilroberta-base",
    onnx_subdir="onnx",
)


def _hf_urls(spec: _ModelSpec) -> tuple[Dict[str, str], Dict[str, str]]:
    """Return (model_files, tokenizer_files) URL dicts for a model spec."""
    base = f"https://huggingface.co/{spec.hf_repo}/resolve/main"
    onnx_base = f"{base}/{spec.onnx_subdir}" if spec.onnx_subdir else base
    model_files = {"model.onnx": f"{onnx_base}/model.onnx"}
    tokenizer_files = {
        "tokenizer.json": f"{base}/tokenizer.json",
        "tokenizer_config.json": f"{base}/tokenizer_config.json",
        "special_tokens_map.json": f"{base}/special_tokens_map.json",
    }
    return model_files, tokenizer_files


def _download_file(url: str, dest: str, desc: str) -> None:
    """Download a file with progress indication."""
    req = Request(url, headers={"User-Agent": "lore-sdk/0.1"})
    response = urlopen(req, timeout=60)  # noqa: S310
    total = response.headers.get("Content-Length")
    total_bytes = int(total) if total else None

    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(".tmp")

    downloaded = 0
    chunk_size = 128 * 1024  # 128KB

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


def _ensure_model(
    spec: _ModelSpec = PROSE_MODEL,
    model_dir: Optional[str] = None,
) -> str:
    """Ensure model files exist, downloading if needed. Returns model directory."""
    base = model_dir or _MODEL_DIR
    model_path = os.path.join(base, spec.name)

    # Check if model.onnx exists as readiness marker
    onnx_path = os.path.join(model_path, "model.onnx")
    if os.path.exists(onnx_path):
        return model_path

    sys.stderr.write(
        f"Lore: downloading embedding model ({spec.name})...\n"
    )

    model_files, tokenizer_files = _hf_urls(spec)
    all_files = {**model_files, **tokenizer_files}
    for filename, url in all_files.items():
        dest = os.path.join(model_path, filename)
        if not os.path.exists(dest):
            _download_file(url, dest, filename)

    sys.stderr.write("Lore: model ready.\n")
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


class LocalEmbedder(Embedder):
    """Local embedding engine using ONNX sentence-transformer models.

    Downloads the model on first use and caches it to ``~/.lore/models/``.

    Parameters
    ----------
    model_dir:
        Override the default model cache directory.
    model_spec:
        A ``_ModelSpec`` describing which model to use.  Defaults to
        :data:`PROSE_MODEL` (``all-MiniLM-L6-v2``).
    """

    def __init__(
        self,
        model_dir: Optional[str] = None,
        model_spec: _ModelSpec = PROSE_MODEL,
    ) -> None:
        self._model_dir = model_dir
        self._model_spec = model_spec
        self._session = None
        self._tokenizer = None

    def _load(self) -> None:
        """Lazy-load model and tokenizer."""
        if self._session is not None:
            return

        import onnxruntime as ort  # type: ignore[import-untyped]
        from tokenizers import Tokenizer  # type: ignore[import-untyped]

        model_path = _ensure_model(self._model_spec, self._model_dir)

        self._session = ort.InferenceSession(
            os.path.join(model_path, "model.onnx"),
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = Tokenizer.from_file(
            os.path.join(model_path, "tokenizer.json")
        )
        # Max sequence length
        self._tokenizer.enable_truncation(max_length=256)
        self._tokenizer.enable_padding(length=256)

    def embed(self, text: str) -> List[float]:
        """Embed a single text string."""
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts."""
        if not texts:
            return []

        self._load()
        assert self._tokenizer is not None
        assert self._session is not None

        encodings = self._tokenizer.encode_batch(texts)
        input_ids = np.array(
            [e.ids for e in encodings], dtype=np.int64
        )
        attention_mask = np.array(
            [e.attention_mask for e in encodings], dtype=np.int64
        )
        token_type_ids = np.zeros_like(input_ids)

        outputs = self._session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )

        # outputs[0] is token embeddings: (batch, seq_len, hidden_dim)
        token_embeddings = outputs[0]
        pooled = _mean_pooling(token_embeddings, attention_mask)
        normalized = _normalize(pooled)

        return [vec.tolist() for vec in normalized]


def make_code_embedder(
    model_dir: Optional[str] = None,
    fallback: Optional[Embedder] = None,
) -> Embedder:
    """Create a code-specialized embedder, falling back to *fallback* on error.

    If the code model cannot be downloaded or loaded, returns *fallback*
    (or a default prose :class:`LocalEmbedder`) so the system degrades
    gracefully.
    """
    try:
        embedder = LocalEmbedder(model_dir=model_dir, model_spec=CODE_MODEL)
        # Force a load to verify the model works
        embedder._load()
        return embedder
    except Exception:
        logger.warning(
            "Code embedding model unavailable — falling back to prose model."
        )
        return fallback or LocalEmbedder(model_dir=model_dir)
