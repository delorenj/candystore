"""Candystore — durable event store + Dapr subscriber for the 33GOD platform.

Subscribes to Claude Code agent.* events on the bloodbank-pubsub Dapr
component, persists CloudEvents envelopes to Postgres, and exposes a
read-only REST API for queries.
"""

__version__ = "0.1.0"
