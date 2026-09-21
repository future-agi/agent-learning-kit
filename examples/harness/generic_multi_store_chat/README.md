# Generic multi-store chat fixture

This fixture is a release-conformance agent, not a product example. It proves that source-owned
Compose topology can be compiled without repository-specific logic when one agent depends on both
PostgreSQL and Redis. The HTTP response reads durable account state from PostgreSQL and increments
an ephemeral access counter in Redis.

The hosted harness replaces Compose service DNS and ports with isolated runtime capabilities. No
environment URL in this repository is valid inside Daytona without that compilation step.
