"""Deterministic fake embedding providers for network-free tests.

Vectors are derived from a fixed vocabulary via term frequency, so cosine
similarity is stable and each test controls exactly which texts are "close".
"""

import math

from app.llm import AssistantOutput, LLMUnavailableError

VOCAB = ["fee", "cap", "harbor", "merger", "parking", "ticket", "alpha", "beta"]

# Synonyms collapse onto a vocab term, so a query can be semantically close to
# a document while sharing NO literal keyword with it — exactly the gap that
# semantic retrieval is meant to close.
SYNONYMS = {
    "fee": ["fee", "cost", "price", "charge"],
    "cap": ["cap", "ceiling", "limit", "maximum"],
    "harbor": ["harbor", "harbour", "port"],
    "merger": ["merger", "acquisition", "combination"],
    "parking": ["parking", "vehicle"],
    "ticket": ["ticket", "citation", "fine"],
    "alpha": ["alpha"],
    "beta": ["beta"],
}


def _vector(text: str) -> list[float]:
    lowered = text.lower()
    # Weighted term frequency over a fixed vocabulary, with synonyms folded in.
    counts: list[float] = []
    for word in VOCAB:
        total = sum(lowered.count(form) for form in SYNONYMS.get(word, [word]))
        counts.append(4.0 * total)
    counts.append(0.05)  # small bias keeps vectors non-zero
    norm = math.sqrt(sum(c * c for c in counts))
    return [c / norm for c in counts]


class FakeEmbedder:
    """Deterministic embedder with a fixed model name and dimension."""

    def __init__(self, model: str = "fake-embed", dimension: int | None = None) -> None:
        self.embed_model = model
        self.dimension = dimension
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        vectors = [_vector(text) for text in texts]
        if vectors:
            self.dimension = len(vectors[0])
        return vectors


class BrokenEmbedder:
    """Embedder that always fails, like an unreachable Ollama."""

    embed_model = "fake-embed"
    dimension = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise LLMUnavailableError("Could not reach the embedding service.")


class WrongDimensionEmbedder:
    """Embedder whose dimension differs from previously stored vectors."""

    def __init__(self, model: str = "fake-embed") -> None:
        self.embed_model = model
        self.dimension: int | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = [_vector(text) for text in texts]
        if vectors:
            self.dimension = len(vectors[0]) + 1  # deliberately incompatible
            vectors = [v + [0.05] for v in vectors]
        return vectors


class FakeAssistant:
    """Assistant double recording composed prompts."""

    assistant_model = "fake-answer-model"

    def __init__(self, text: str = "Answer from evidence.") -> None:
        self.text = text
        self.received: list[str] = []

    def answer(self, prompt: str) -> AssistantOutput:
        self.received.append(prompt)
        return AssistantOutput(text=self.text, model=self.assistant_model)
