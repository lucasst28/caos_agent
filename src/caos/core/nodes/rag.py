
import structlog
from typing import Any
try:
    from sentence_transformers import SentenceTransformer, util
except ImportError:
    SentenceTransformer = None
    util = None

logger = structlog.get_logger(__name__)

class RagEngine:
    """Retrieval-Augmented Generation Engine (Lightweight).
    
    Manages an in-memory vector store using SentenceTransformers
    to index and retrieve technical manuals.
    """
    
    def __init__(self, persist_path: str = "./caos_memory"):
        self.persist_path = persist_path
        self.documents: list[str] = []
        self.metadatas: list[dict[str, Any]] = []
        self.embeddings = None
        self.model = None
        
        try:
            if SentenceTransformer:
                # Use a small, efficient model
                self.model = SentenceTransformer('all-MiniLM-L6-v2')
            else:
                logger.warning("rag_init_failed", error="sentence-transformers not installed")
            
            # Try loading existing index
            self.load_from_disk()

        except Exception as e:
            logger.error("rag_init_failed", error=str(e))

    def add_document(self, text: str, metadata: dict[str, Any], doc_id: str) -> bool:
        """Add a document to the in-memory store."""
        if not self.model:
            return False
        try:
            self.documents.append(text)
            self.metadatas.append(metadata)
            
            # Re-compute embeddings for simplicity in this PoC
            # For production, we would append incrementally.
            # Convert to tensor and detach to avoid grad if any
            self.embeddings = self.model.encode(self.documents, convert_to_tensor=True)
            logger.info("rag_doc_added", doc_id=doc_id)
            
            # Auto-save
            self.save_to_disk()
            return True
        except Exception as e:
            logger.error("rag_add_failed", error=str(e))
            return False

    def query(self, text: str, k: int = 2) -> list[str]:
        """Retrieve top-k relevant document chunks."""
        if not self.model or self.embeddings is None or len(self.documents) == 0:
            return []
        try:
            query_embedding = self.model.encode(text, convert_to_tensor=True)
            hits = util.semantic_search(query_embedding, self.embeddings, top_k=k)
            
            # hits is list of list: [[{'corpus_id': 0, 'score': 0.8}, ...]]
            results = []
            if hits and len(hits) > 0:
                for hit in hits[0]:
                    doc_idx = hit['corpus_id']
                    results.append(self.documents[doc_idx])
            return results
        except Exception as e:
            logger.error("rag_query_failed", error=str(e))
            return []

    def save_to_disk(self) -> bool:
        """Persist index to disk."""
        import pickle
        import os
        try:
            if not os.path.exists(self.persist_path):
                os.makedirs(self.persist_path)
            
            with open(f"{self.persist_path}/index.pkl", "wb") as f:
                pickle.dump({
                    "documents": self.documents,
                    "metadatas": self.metadatas,
                    "embeddings": self.embeddings
                }, f)
            logger.info("rag_index_saved", path=self.persist_path)
            return True
        except Exception as e:
            logger.error("rag_save_failed", error=str(e))
            return False

    def load_from_disk(self) -> bool:
        """Load index from disk."""
        import pickle
        import os
        try:
            path = f"{self.persist_path}/index.pkl"
            if not os.path.exists(path):
                return False
            
            with open(path, "rb") as f:
                data = pickle.load(f)
                self.documents = data["documents"]
                self.metadatas = data["metadatas"]
                self.embeddings = data["embeddings"]
            logger.info("rag_index_loaded", path=path, count=len(self.documents))
            return True
        except Exception as e:
            logger.error("rag_load_failed", error=str(e))
            return False

# Singleton
_rag_engine = None

def get_rag_engine() -> RagEngine:
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = RagEngine()
    return _rag_engine
