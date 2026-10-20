# NgramDB graph and file index

Cron-five and ngram-hook-add node. Uses the [web CLI adapter](../20261020183846-e29da0472f9a2be36f01)
to materialize stable graph and safe file-manifest JSON to
`$NGRAM_WEB_STATE/graph-index.json`. No private graph snapshot is published
anonymously. `NGRAM_WEB_PYTHON` must point to the web node's Python environment.
The API uses the same adapter and a short-lived cache for live discovery.
