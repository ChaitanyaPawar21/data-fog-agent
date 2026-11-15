import os

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
JWT_SECRET = os.environ["JWT_SECRET"]

# Browsers accept secure cookies on http://localhost, except Safari. Set false for local dev.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() == "true"

# Fernet key that encrypts source connection settings (they hold passwords).
CONFIG_ENCRYPTION_KEY = os.environ["CONFIG_ENCRYPTION_KEY"]

# Embeddings use any OpenAI-compatible API, Gemini's by default. They are skipped when there is
# no key, so the app still runs without one (search then matches keywords only).
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "")
EMBEDDING_BASE_URL = (
    os.environ.get("EMBEDDING_BASE_URL")
    or "https://generativelanguage.googleapis.com/v1beta/openai/"
)
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL") or "gemini-embedding-2"

# Chat talks to any OpenAI-compatible API, NVIDIA's by default. The model must support tool
# calling. Without a key the chat API answers 503.
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL") or "https://integrate.api.nvidia.com/v1"
LLM_MODEL = os.environ.get("LLM_MODEL") or "nvidia/nemotron-3.5-lightning-30b-a3b"

# Optional LLM tracing. On when both keys are set (see llm.py).
LANGFUSE_ENABLED = bool(
    os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
)
# Langfuse reads LANGFUSE_HOST itself. Left empty in .env it would be used as the address,
# so an empty one is removed and Langfuse falls back to its cloud address.
if not os.environ.get("LANGFUSE_HOST"):
    os.environ.pop("LANGFUSE_HOST", None)

# Auto-Docs is one JSON answer per asset, so a small fast model is enough. Same base URL as
# chat; each setting falls back to the chat one when it's empty.
DOCS_API_KEY = os.environ.get("DOCS_API_KEY") or LLM_API_KEY
DOCS_MODEL = os.environ.get("DOCS_MODEL") or LLM_MODEL
