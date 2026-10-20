"""Ollama availability endpoint (never raises — reports honestly)."""

from fastapi import APIRouter, Depends

from ..llm import LLMUnavailableError, embed_model_name, list_models, summary_model_name
from ..schemas import OllamaHealth
from ..security import require_user

router = APIRouter(
    prefix="/ollama", tags=["ollama"], dependencies=[Depends(require_user)]
)


@router.get("/health", response_model=OllamaHealth)
def ollama_health() -> OllamaHealth:
    """Report local Ollama availability.

    Always returns HTTP 200: `available: false` with an empty model list when
    the service cannot be reached.
    """
    summary_model = summary_model_name()
    embed_model = embed_model_name()
    try:
        models = list_models()
    except LLMUnavailableError:
        return OllamaHealth(
            available=False, models=[], summary_model=summary_model,
            embed_model=embed_model,
        )
    return OllamaHealth(
        available=True, models=models, summary_model=summary_model,
        embed_model=embed_model,
    )
