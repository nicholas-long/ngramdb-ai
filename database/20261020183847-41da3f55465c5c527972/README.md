# Codex task worker (opt-in)

Separate service node for the [web task inbox](../20261020183846-e29da0472f9a2be36f01). Disabled unless
`NGRAM_CODEX_WORKER_ENABLED=1`; requires distinct configured Unix worker/web
identities. See the web README for setup, shared ACLs, sandbox modes,
review workflow, cancellation and timeout behavior. No worker run executes
from a web request. Deploy under a dedicated unprivileged account.
