
import pytest
import os
import shutil
from unittest.mock import MagicMock, patch
from caos.core.nodes.rag import RagEngine
from caos.safety.runtime import CircuitBreaker, MemoryBackend, RedisBackend

@pytest.fixture
def clean_rag_dir():
    path = "./test_rag_data"
    if os.path.exists(path):
        shutil.rmtree(path)
    yield path
    if os.path.exists(path):
        shutil.rmtree(path)

def test_rag_persistence(clean_rag_dir):
    # 1. Create and Seed
    rag1 = RagEngine(persist_path=clean_rag_dir)
    rag1.add_document("Doc A", {"id": 1}, "doc_1")
    assert len(rag1.documents) == 1
    
    # 2. Re-instantiate (simulate restart)
    rag2 = RagEngine(persist_path=clean_rag_dir)
    # It should have auto-loaded
    assert len(rag2.documents) == 1
    assert rag2.documents[0] == "Doc A"
    
    # 3. Query works on loaded data
    results = rag2.query("Doc A", k=1)
    assert len(results) == 1
    assert results[0] == "Doc A"

def test_circuit_breaker_memory_backend():
    cb = CircuitBreaker(backend=MemoryBackend(), max_tokens=100)
    cb.record_usage(50, 0.0)
    assert cb.get_status()["tokens_used"] == 50
    assert not cb.is_tripped()
    
    cb.record_usage(60, 0.0)
    assert cb.get_status()["tokens_used"] == 110
    assert cb.is_tripped()

@patch("redis.from_url")
def test_circuit_breaker_redis_backend(mock_redis_cls):
    # Setup mock
    mock_client = MagicMock()
    mock_redis_cls.return_value = mock_client
    
    # Mock mget return values (tokens, cost)
    # First call: 0, 0.0
    # Second call: 50, 0.0
    mock_client.mget.side_effect = [
        (b"0", b"0.0"),    # Initial status
        (b"50", b"0.0"),   # After update (simulated)
    ]
    
    backend = RedisBackend("redis://localhost:6379")
    cb = CircuitBreaker(backend=backend, max_tokens=100)
    
    # Test Interaction
    cb.record_usage(50, 0.0)
    
    # Verify Redis calls
    mock_client.pipeline.assert_called()
    pipe = mock_client.pipeline.return_value
    pipe.incrby.assert_called() # tokens
    pipe.execute.assert_called()
