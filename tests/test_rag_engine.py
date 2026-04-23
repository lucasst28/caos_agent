
import pytest
from caos.core.nodes.rag import RagEngine

@pytest.fixture
def rag():
    return RagEngine()  # In-memory by default now

def test_rag_ingest_and_query(rag):
    # Ingest diverse manuals
    if not rag.model:
        pytest.skip("sentence-transformers not installed")

    rag.add_document(
        "Cooling System Manual: If temperature exceeds 100C, check coolant level and fan operation.",
        {"topic": "cooling"},
        "doc_cool_01"
    )
    rag.add_document(
        "Braking System Manual: If vibration is felt during braking, check pads and rotors.",
        {"topic": "braking"},
        "doc_brake_01"
    )
    rag.add_document(
        "Network Config: Ensure IP address is static and gateway is reachable.",
        {"topic": "network"},
        "doc_net_01"
    )

    # Query for temperature issues
    results = rag.query("engine overheating temperature high", k=1)
    
    assert len(results) == 1
    assert "Cooling System" in results[0]
    assert "coolant level" in results[0]

    # Query for vibration
    results = rag.query("shaking vibration check", k=1)
    assert len(results) == 1
    assert "Braking System" in results[0]
