"""Local deterministic and Hugging Face embedding functions for EDRAK RAG subsystem.

Provides high-quality local Hugging Face static embeddings (e.g., sentence-transformers/all-MiniLM-L6-v2)
without requiring cloud API calls, ensuring identical vector geometry across all queries and document indexing.
"""

import hashlib
import logging
import math
from pathlib import Path
import re
from typing import Any, List, Optional, Union
import numpy as np

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from edrak.core.config import settings

logger = logging.getLogger(__name__)


class FastLocalEmbeddingFunction(EmbeddingFunction[Documents]):
    """Lightweight, offline deterministic embedding function for ChromaDB.

    Generates dense normalized 384-dimensional semantic-hash vectors.
    """

    def __init__(self, dimension: int = 384):
        self.dimension = dimension

    def __call__(self, input: Documents) -> Embeddings:
        embeddings: Embeddings = []
        for text in input:
            vec = np.zeros(self.dimension, dtype=np.float32)
            if not text:
                embeddings.append(vec.tolist())
                continue

            # Tokenize into words and char 3-grams
            words = re.findall(r"\w+", text.lower())
            for word in words:
                # Word hash
                h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
                idx = h % self.dimension
                sign = 1.0 if (h & 1) else -1.0
                vec[idx] += sign * 1.5

                # Character n-grams for subword similarity
                if len(word) >= 3:
                    for i in range(len(word) - 2):
                        ngram = word[i : i + 3]
                        nh = int(hashlib.md5(ngram.encode("utf-8")).hexdigest(), 16)
                        nidx = nh % self.dimension
                        nsign = 1.0 if (nh & 1) else -1.0
                        vec[nidx] += nsign * 0.5

            # Normalize vector to unit length (L2 norm)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm

            embeddings.append(vec.tolist())
        return embeddings


class HuggingFaceLocalEmbeddingFunction(EmbeddingFunction[Documents]):
    """Embeddings powered by local Hugging Face ONNX / SentenceTransformers models.

    Supports 'sentence-transformers/all-MiniLM-L6-v2', 'BAAI/bge-small-en-v1.5', etc.
    """

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or settings.EMBEDDING_MODEL_NAME
        self._delegate: Optional[Any] = None
        self._init_delegate()

    def _init_delegate(self):
        # 1. Try ONNX runtime embedding (fast, self-contained, no PyTorch overhead)
        try:
            from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
            if "minilm" in self.model_name.lower():
                self._delegate = ONNXMiniLM_L6_V2()
                logger.info("Initialized local HuggingFace ONNX model: %s", self.model_name)
                return
        except Exception as e:
            logger.debug("ONNXMiniLM_L6_V2 not initialized: %s", e)

        # 2. Try SentenceTransformers if installed
        try:
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
            self._delegate = SentenceTransformerEmbeddingFunction(model_name=self.model_name)
            logger.info("Initialized SentenceTransformerEmbeddingFunction with: %s", self.model_name)
            return
        except Exception as e:
            logger.debug("SentenceTransformerEmbeddingFunction not available: %s", e)

        # 3. Fallback to deterministic FastLocalEmbeddingFunction
        if not getattr(settings, "EMBEDDING_ALLOW_FALLBACK", False):
            raise RuntimeError(
                f"Failed to initialize HuggingFace/ONNX embedding model '{self.model_name}' "
                "and EMBEDDING_ALLOW_FALLBACK is False. Install onnxruntime or sentence-transformers."
            )
        logger.warning(
            "HuggingFace embedding libraries not ready, using fast deterministic fallback for '%s'",
            self.model_name,
        )
        self._delegate = FastLocalEmbeddingFunction(dimension=settings.EMBEDDING_DIMENSION)

    def __call__(self, input: Documents) -> Embeddings:
        if self._delegate is None:
            self._init_delegate()
        return self._delegate(input)


def get_embedding_function(
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
) -> EmbeddingFunction[Documents]:
    """Factory function returning the configured embedding function."""
    prov = (provider or settings.EMBEDDING_PROVIDER).lower()
    m_name = model_name or settings.EMBEDDING_MODEL_NAME

    if prov in ("huggingface", "hf", "onnx", "sentence-transformers"):
        # 1. Prefer Chroma's native ONNX MiniLM for all-MiniLM-L6-v2
        if "minilm" in m_name.lower():
            try:
                from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
                return ONNXMiniLM_L6_V2()
            except Exception as e:
                logger.warning("Failed loading ONNXMiniLM_L6_V2: %s", e)

        # 2. Try SentenceTransformers if installed
        try:
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
            return SentenceTransformerEmbeddingFunction(model_name=m_name)
        except Exception as e:
            logger.warning("SentenceTransformerEmbeddingFunction not available: %s", e)

        # 3. Fallback check
        if not getattr(settings, "EMBEDDING_ALLOW_FALLBACK", False):
            raise RuntimeError(
                f"Configured embedding provider '{prov}' could not load model '{m_name}' "
                "and EMBEDDING_ALLOW_FALLBACK is False."
            )
        logger.warning("Falling back to FastLocalEmbeddingFunction for provider '%s'", prov)
        return FastLocalEmbeddingFunction(dimension=settings.EMBEDDING_DIMENSION)

    elif prov in ("fast", "local_fast", "mock"):
        return FastLocalEmbeddingFunction(dimension=settings.EMBEDDING_DIMENSION)
    else:
        try:
            from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
            return ONNXMiniLM_L6_V2()
        except Exception as e:
            logger.warning("Failed loading default ONNXMiniLM_L6_V2: %s", e)
            if not getattr(settings, "EMBEDDING_ALLOW_FALLBACK", False):
                raise RuntimeError(
                    "Default embedding model failed to load and EMBEDDING_ALLOW_FALLBACK is False."
                ) from e
            return FastLocalEmbeddingFunction(dimension=settings.EMBEDDING_DIMENSION)
