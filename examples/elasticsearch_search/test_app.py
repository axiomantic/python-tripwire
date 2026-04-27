"""Test Elasticsearch error log search using tripwire elasticsearch_mock."""

import tripwire

from .app import search_error_logs


def test_search_error_logs():
    tripwire.elasticsearch_mock.mock_operation(
        "search",
        returns={
            "hits": {
                "total": {"value": 2},
                "hits": [
                    {"_id": "1", "_source": {"level": "error", "message": "timeout"}},
                    {"_id": "2", "_source": {"level": "error", "message": "connection refused"}},
                ],
            }
        },
    )

    with tripwire:
        from elasticsearch import Elasticsearch
        es = Elasticsearch("http://localhost:9200")
        logs = search_error_logs(es, "app-logs", hours=12, max_results=50)

    assert len(logs) == 2
    assert logs[0]["message"] == "timeout"
    assert logs[1]["message"] == "connection refused"

    tripwire.elasticsearch_mock.assert_search(
        index="app-logs",
        query={
            "bool": {
                "must": [
                    {"match": {"level": "error"}},
                    {"range": {"timestamp": {"gte": "now-12h"}}},
                ],
            }
        },
        size=50,
    )
