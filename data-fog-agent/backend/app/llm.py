"""Embeddings (Gemini by default, see config). Chat lives in agent.py.

Never pass credentials or unmasked personal data to these functions.
"""

from openai import OpenAI

from app import config
from app.models import EMBEDDING_DIM


def embed_texts(texts: list[str]) -> list[list[float]]:
    client = OpenAI(api_key=config.EMBEDDING_API_KEY, base_url=config.EMBEDDING_BASE_URL)
    # gemini-embedding-2 defaults to 3072 dimensions. Asking for 1536 matches the vector
    # column, and the model returns them already normalized.
    response = client.embeddings.create(
        model=config.EMBEDDING_MODEL, input=texts, dimensions=EMBEDDING_DIM
    )
    return [item.embedding for item in response.data]
