"""Error log search with Elasticsearch."""



def search_error_logs(es, index_name, hours=24, max_results=100):
    """Search for recent error-level log entries."""
    result = es.search(
        index=index_name,
        query={
            "bool": {
                "must": [
                    {"match": {"level": "error"}},
                    {"range": {"timestamp": {"gte": f"now-{hours}h"}}},
                ],
            }
        },
        size=max_results,
    )
    return [hit["_source"] for hit in result["hits"]["hits"]]
