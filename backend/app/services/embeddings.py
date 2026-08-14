"""Local sentence embeddings for the brain — vectors never leave the machine.

Retrieval is brute-force cosine over numpy: at personal scale (hundreds of
entries) this is microseconds, so no vector-index dependency is warranted.
Vectors are L2-normalized at embed time, making dot product == cosine.
"""

from functools import lru_cache

import numpy as np

MODEL_NAME = "all-MiniLM-L6-v2"
EMBED_DIM = 384


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer  # deferred: heavy import

    return SentenceTransformer(MODEL_NAME)


def embed_text(text: str) -> bytes:
    vec = _model().encode([text], normalize_embeddings=True)[0]
    return np.asarray(vec, dtype=np.float32).tobytes()


def cosine_scores(query: bytes, candidates: list[bytes]) -> list[float]:
    if not candidates:
        return []
    q = np.frombuffer(query, dtype=np.float32)
    matrix = np.vstack([np.frombuffer(c, dtype=np.float32) for c in candidates])
    return (matrix @ q).tolist()
