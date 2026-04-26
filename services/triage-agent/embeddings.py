import hashlib
from shared.logger import get_logger

log = get_logger("triage-embeddings")

class EmbeddingService:
    def __init__(self):
        log.warning("using_mocked_embeddings", 
                    reason="No embedding API key provided. Using deterministic mock for local development.")

    def get_embedding(self, text: str) -> list[float]:
        """
        Mock embedding generator that returns a deterministic 1536-dimensional float vector
        so pgvector doesn't crash while we test locally.
        
        In production, this should call OpenAI's text-embedding-3-small or Voyage AI via Anthropic.
        """
        if not text:
            return [0.0] * 1536
            
        vector = []
        base_hash = hashlib.sha256(text.encode("utf-8")).digest()
        
        # Expand 32 byte hash into 1536 floats between -1.0 and 1.0
        # It's totally meaningless semantically, but it's consistent for the same string!
        for i in range(1536):
            byte_val = base_hash[i % 32]
            # Normalize byte (0-255) to float (-1.0 to +1.0)
            mock_val = (byte_val / 127.5) - 1.0
            vector.append(float(mock_val))
            
        return vector
