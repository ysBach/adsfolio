import unittest

from adsfolio.api.search import SearchAPI


class DummyBaseClient:
    def __init__(self, docs):
        self._docs = docs
        self.calls = []

    def get(self, endpoint, params=None):
        assert endpoint == "/search/query"
        assert params is not None
        self.calls.append(dict(params))

        start = int(params["start"])
        rows = int(params["rows"])
        page_docs = self._docs[start : start + rows]
        return {
            "response": {
                "numFound": len(self._docs),
                "start": start,
                "docs": page_docs,
            },
            "responseHeader": {"QTime": 1},
        }

    def post_raw(self, endpoint, data=None, params=None, headers=None):
        self.calls.append(
            {
                "endpoint": endpoint,
                "data": data,
                "params": params,
                "headers": headers,
            }
        )
        return {
            "response": {
                "numFound": 2,
                "start": 0,
                "docs": [{"bibcode": "B1"}, {"bibcode": "B2"}],
            },
            "responseHeader": {"QTime": 1},
        }


class TestQueryAll(unittest.TestCase):
    def test_query_all_paginates_until_complete(self):
        all_docs = [{"bibcode": f"B{i}"} for i in range(5)]
        client = DummyBaseClient(all_docs)
        api = SearchAPI(client)

        result = api.query_all(
            q="author:Einstein",
            fl=["bibcode"],
            page_size=2,
            sort="date desc",
        )

        self.assertEqual(result.num_found, 5)
        self.assertEqual(len(result.docs), 5)
        self.assertEqual(
            [d["bibcode"] for d in result.docs], [f"B{i}" for i in range(5)]
        )
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(client.calls[0]["start"], 0)
        self.assertEqual(client.calls[1]["start"], 2)
        self.assertEqual(client.calls[2]["start"], 4)

    def test_query_all_respects_max_records(self):
        all_docs = [{"bibcode": f"B{i}"} for i in range(10)]
        client = DummyBaseClient(all_docs)
        api = SearchAPI(client)

        result = api.query_all(
            q="author:Einstein",
            fl=["bibcode"],
            page_size=4,
            max_records=6,
        )

        self.assertEqual(result.num_found, 10)
        self.assertEqual(len(result.docs), 6)
        self.assertEqual(
            [d["bibcode"] for d in result.docs], [f"B{i}" for i in range(6)]
        )

    def test_bigquery_uses_base_client_post_raw(self):
        client = DummyBaseClient([])
        api = SearchAPI(client)

        result = api.bigquery(
            bibcodes=["B1", "B2"],
            fl=["bibcode", "title"],
        )

        self.assertEqual(result.num_found, 2)
        self.assertEqual([doc["bibcode"] for doc in result.docs], ["B1", "B2"])
        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call["endpoint"], "/search/bigquery")
        self.assertEqual(call["params"]["q"], "*:*")
        self.assertEqual(call["params"]["fl"], "bibcode,title")
        self.assertEqual(call["params"]["rows"], 2)
        self.assertEqual(call["data"], "bibcode\nB1\nB2")
        self.assertEqual(call["headers"]["Content-Type"], "text/plain")


if __name__ == "__main__":
    unittest.main()
