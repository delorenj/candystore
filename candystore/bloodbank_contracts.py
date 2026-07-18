"""Fail-closed validation for authoritative Lifecycle projection inputs.

Bloodbank owns these schemas. Candystore reads the exact checked-out schema
tree (or the explicitly mounted tree in a container) and adds only the
service-identity checks that distinguish Lifecycle authority publications from
structurally valid envelopes produced by another actor.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

logger = logging.getLogger("candystore.lifecycle.contracts")

SNAPSHOT_TYPE = "bloodbank.v1.lifecycle.snapshot.updated"
SNAPSHOT_SUBJECT = "bloodbank.evt.v1.lifecycle.snapshot.updated"
SNAPSHOT_SCHEMA_REF = "bloodbank.v1.lifecycle.snapshot.updated.v3"
SNAPSHOT_SCHEMA_PATH = "bloodbank/v1/lifecycle/snapshot.updated.v3.json"

INTENT_TYPE = "bloodbank.v1.lifecycle.intent.submit"
INTENT_REPLY_SUBJECT = "bloodbank.rpy.v1.lifecycle.intent.submit"
INTENT_REPLY_SCHEMA_REF = "bloodbank.v1.lifecycle.intent.submit.reply.v1"
INTENT_REPLY_SCHEMA_PATH = "bloodbank/v1/lifecycle/intent.submit.reply.v1.json"

AUTHORITY_SOURCE = "urn:33god:service:lifecycle"
AUTHORITY_PRODUCER = "delorenj/lifecycle"
AUTHORITY_SERVICE = "lifecycle"
AUTHORITY_ACTOR_TYPE = "service"
AUTHORITY_ACTOR_ID = "delorenj.lifecycle"

ProjectionKind = Literal["snapshot", "verdict"]


class AuthorityContractError(ValueError):
    """A candidate publication cannot authorize a Lifecycle projection."""


class SchemaRegistryError(RuntimeError):
    """The canonical Bloodbank schema registry is operationally unavailable."""


def validate_projection_candidate(envelope: dict[str, Any]) -> ProjectionKind | None:
    """Return the projection kind after exact schema and authority validation.

    Non-Lifecycle traffic returns ``None``. Anything that presents itself as a
    Lifecycle snapshot or reply candidate is validated fail closed.
    """

    projection_kind = _candidate_kind(envelope)
    if projection_kind is None:
        return None

    schema_path = (
        SNAPSHOT_SCHEMA_PATH if projection_kind == "snapshot" else INTENT_REPLY_SCHEMA_PATH
    )
    try:
        validator = _validator(schema_path)
        validator.validate(envelope)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "envelope"
        raise AuthorityContractError(
            f"canonical Bloodbank {projection_kind} schema rejected {location} "
            f"({exc.validator} constraint)"
        ) from exc
    except SchemaRegistryError:
        raise
    except Exception as exc:
        raise SchemaRegistryError(
            f"canonical Bloodbank {projection_kind} schema registry failed during validation"
        ) from exc

    _validate_authority_identity(envelope)
    data = _object(envelope.get("data"), "data")
    lifecycle_id = _text(data.get("lifecycle_id"), "data.lifecycle_id")

    if projection_kind == "snapshot":
        if envelope.get("ordering_key") != f"lifecycle:{lifecycle_id}":
            raise AuthorityContractError("snapshot ordering_key does not match lifecycle_id")
        provenance = _object(data.get("provenance"), "data.provenance")
        if provenance.get("authority") != AUTHORITY_PRODUCER:
            raise AuthorityContractError("snapshot provenance authority is not Lifecycle")
        actor = _object(envelope.get("actor"), "actor")
        if _text(actor.get("instance"), "actor.instance") != _text(
            provenance.get("authority_instance"),
            "data.provenance.authority_instance",
        ):
            raise AuthorityContractError(
                "snapshot actor instance does not match provenance authority_instance"
            )
        publication = _object(data.get("publication"), "data.publication")
        if publication.get("aggregate_id") != lifecycle_id:
            raise AuthorityContractError("snapshot publication aggregate_id does not match")
        if publication.get("aggregate_version") != data.get("state_version"):
            raise AuthorityContractError("snapshot aggregate_version does not match state_version")
    else:
        if envelope.get("causationid") != data.get("reply_to_command_event_id"):
            raise AuthorityContractError("reply causationid is not its command event ID")

    return projection_kind


def log_rejection(envelope: dict[str, Any], error: AuthorityContractError) -> None:
    """Log bounded rejection metadata without dumping the untrusted envelope."""

    logger.warning(
        "excluded invalid Lifecycle authority candidate id=%s type=%s subject=%s: %s",
        envelope.get("id"),
        envelope.get("type"),
        envelope.get("subject"),
        error,
    )


def _candidate_kind(envelope: dict[str, Any]) -> ProjectionKind | None:
    if (
        envelope.get("type") == SNAPSHOT_TYPE
        or envelope.get("subject") == SNAPSHOT_SUBJECT
        or envelope.get("schemaref") == SNAPSHOT_SCHEMA_REF
    ):
        return "snapshot"
    if (
        envelope.get("subject") == INTENT_REPLY_SUBJECT
        or envelope.get("schemaref") == INTENT_REPLY_SCHEMA_REF
        or (envelope.get("type") == INTENT_TYPE and envelope.get("kind") == "reply")
    ):
        return "verdict"
    return None


def _validate_authority_identity(envelope: dict[str, Any]) -> None:
    expected = {
        "source": AUTHORITY_SOURCE,
        "producer": AUTHORITY_PRODUCER,
        "service": AUTHORITY_SERVICE,
        "domain": "lifecycle",
    }
    for field, value in expected.items():
        if envelope.get(field) != value:
            raise AuthorityContractError(f"{field} is not canonical Lifecycle authority")
    actor = _object(envelope.get("actor"), "actor")
    if actor.get("type") != AUTHORITY_ACTOR_TYPE or actor.get("agent_id") != AUTHORITY_ACTOR_ID:
        raise AuthorityContractError("actor is not canonical Lifecycle authority")


@lru_cache(maxsize=4)
def _validator(relative_path: str) -> Draft202012Validator:
    try:
        schemas_root = _schemas_root()
        registry = Registry()
        for path in schemas_root.rglob("*.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            if "$id" in document:
                registry = registry.with_resource(document["$id"], Resource.from_contents(document))
        schema = json.loads((schemas_root / relative_path).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(
            schema,
            registry=registry,
            format_checker=FormatChecker(),
        )
    except SchemaRegistryError:
        raise
    except Exception as exc:
        raise SchemaRegistryError(
            f"canonical Bloodbank schema registry could not load {relative_path}"
        ) from exc


def check_projection_registry() -> bool:
    """Load every Lifecycle projection schema used by the running service."""

    _validator(SNAPSHOT_SCHEMA_PATH)
    _validator(INTENT_REPLY_SCHEMA_PATH)
    return True


def _schemas_root() -> Path:
    configured = os.environ.get("BLOODBANK_SCHEMAS_DIR")
    root = (
        Path(configured).expanduser().resolve()
        if configured
        else Path(__file__).resolve().parents[2] / "bloodbank" / "schemas"
    )
    if not root.is_dir():
        raise SchemaRegistryError(
            "canonical Bloodbank schema tree unavailable; set BLOODBANK_SCHEMAS_DIR"
        )
    return root


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AuthorityContractError(f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AuthorityContractError(f"{field} must be non-empty text")
    return value
