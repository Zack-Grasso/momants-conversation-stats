from app import pipeline


class _FakeClient:
    def __init__(self, entries):
        self._entries = entries

    def collect_inbox_entries_by_window(self, agent_id, *, until_date=None, hard_limit=None):
        return self._entries

    def collect_inbox_entries(self, agent_id, limit, *, start_date=None, end_date=None):
        return self._entries


def test_collect_batches_dedupes_conversation_ids():
    entries = [
        {"conversation_id": "a"},
        {"conversation_id": "b"},
        {"conversation_id": "a"},  # duplicate from inbox drift
        {"conversation_id": "c"},
        {"conversation_id": "b"},  # duplicate
    ]
    client = _FakeClient(entries)

    batches, total = pipeline._collect_batches(client, "agent", batch_size=2, max_total=100)

    assert total == 3
    flat = [entry["conversation_id"] for _, batch in batches for entry in batch]
    assert flat == ["a", "b", "c"]


def test_collect_batches_empty_returns_no_batches():
    client = _FakeClient([])
    batches, total = pipeline._collect_batches(client, "agent", batch_size=2, max_total=100)
    assert batches == []
    assert total == 0
