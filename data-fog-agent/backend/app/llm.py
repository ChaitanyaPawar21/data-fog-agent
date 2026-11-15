"""Embeddings (Gemini by default, see config). Chat lives in agent.py.

Never pass credentials or unmasked personal data to these functions.
"""

from app import config
from app.models import EMBEDDING_DIM

# With Langfuse keys set, every model call is traced there. Its client is a drop-in for the
# OpenAI one and reads LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_HOST by itself.
# A trace holds exactly what the model was sent, so it is PII-masked like the request was.
if config.LANGFUSE_ENABLED:
    from langfuse.openai import OpenAI
else:
    from openai import OpenAI


def embed_texts(texts: list[str]) -> list[list[float]]:
    client = OpenAI(api_key=config.EMBEDDING_API_KEY, base_url=config.EMBEDDING_BASE_URL)
    # gemini-embedding-2 defaults to 3072 dimensions. Asking for 1536 matches the vector
    # column, and the model returns them already normalized.
    response = client.embeddings.create(
        model=config.EMBEDDING_MODEL, input=texts, dimensions=EMBEDDING_DIM
    )
    return [item.embedding for item in response.data]
