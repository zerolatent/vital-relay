"""Create allowlisted APNs registration and durable notification outbox.

Revision ID: 0005_responder_notifications
Revises: 0004_protocol_presentations
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_responder_notifications"
down_revision: str | None = "0004_protocol_presentations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    CREATE TABLE responder_push_registrations (
        scope_id UUID NOT NULL,
        registration_id UUID NOT NULL,
        installation_id UUID NOT NULL,
        responder_id UUID NOT NULL,
        platform VARCHAR(16) NOT NULL,
        environment VARCHAR(16) NOT NULL,
        device_token_ciphertext BYTEA NOT NULL,
        device_token_sha256 VARCHAR(64) NOT NULL,
        status VARCHAR(16) NOT NULL,
        authorized_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        revoked_at TIMESTAMP WITH TIME ZONE,
        simulated BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (scope_id, registration_id),
        FOREIGN KEY (scope_id, responder_id)
            REFERENCES responders (scope_id, responder_id)
            ON DELETE CASCADE,
        CONSTRAINT uq_responder_push_registrations_installation
            UNIQUE (scope_id, installation_id),
        CONSTRAINT ck_responder_push_registrations_platform
            CHECK (platform = 'apns'),
        CONSTRAINT ck_responder_push_registrations_environment
            CHECK (environment IN ('sandbox', 'production')),
        CONSTRAINT ck_responder_push_registrations_token_hash
            CHECK (device_token_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_responder_push_registrations_token_ciphertext
            CHECK (octet_length(device_token_ciphertext) > 0),
        CONSTRAINT ck_responder_push_registrations_status
            CHECK (status IN ('active', 'revoked')),
        CONSTRAINT ck_responder_push_registrations_updated_at
            CHECK (updated_at >= authorized_at),
        CONSTRAINT ck_responder_push_registrations_revocation_state CHECK (
            (status = 'active' AND revoked_at IS NULL) OR
            (status = 'revoked' AND revoked_at IS NOT NULL)
        ),
        CONSTRAINT ck_responder_push_registrations_revoked_at
            CHECK (revoked_at IS NULL OR revoked_at >= authorized_at),
        CONSTRAINT ck_responder_push_registrations_not_simulated
            CHECK (simulated = false)
    )
    """,
    """
    CREATE UNIQUE INDEX uq_responder_push_registrations_responder_active
    ON responder_push_registrations (scope_id, responder_id)
    WHERE status = 'active'
    """,
    """
    CREATE UNIQUE INDEX uq_responder_push_registrations_destination_active
    ON responder_push_registrations (
        scope_id, environment, device_token_sha256
    )
    WHERE status = 'active'
    """,
    """
    CREATE TABLE notification_outbox (
        scope_id UUID NOT NULL,
        notification_id UUID NOT NULL,
        invitation_id UUID NOT NULL,
        incident_id UUID NOT NULL,
        responder_id UUID NOT NULL,
        channel VARCHAR(16) NOT NULL,
        template VARCHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL,
        attempt_count INTEGER NOT NULL,
        payload JSONB NOT NULL,
        next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL,
        lease_token UUID,
        lease_until TIMESTAMP WITH TIME ZONE,
        provider_message_id UUID,
        last_error_code VARCHAR(64),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        finalized_at TIMESTAMP WITH TIME ZONE,
        simulated BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (scope_id, notification_id),
        CONSTRAINT fk_notification_outbox_exact_invitation
            FOREIGN KEY (
                scope_id, invitation_id, incident_id, responder_id
            )
            REFERENCES responder_invitations (
                scope_id, invitation_id, incident_id, responder_id
            )
            ON DELETE CASCADE,
        CONSTRAINT uq_notification_outbox_logical_delivery
            UNIQUE (scope_id, invitation_id, channel, template),
        CONSTRAINT uq_notification_outbox_exact_link
            UNIQUE (scope_id, notification_id, invitation_id),
        CONSTRAINT ck_notification_outbox_channel
            CHECK (channel = 'apns'),
        CONSTRAINT ck_notification_outbox_template
            CHECK (template = 'responder_invitation_v1'),
        CONSTRAINT ck_notification_outbox_status CHECK (
            status IN (
                'pending', 'provider_accepted', 'permanent_failed',
                'unavailable', 'unknown'
            )
        ),
        CONSTRAINT ck_notification_outbox_attempt_count
            CHECK (attempt_count >= 0),
        CONSTRAINT ck_notification_outbox_payload CHECK (
            jsonb_typeof(payload) = 'object' AND
            payload ?& ARRAY[
                'schema_version', 'kind', 'incident_id', 'invitation_id'
            ] AND
            payload - 'schema_version' - 'kind' - 'incident_id' -
                'invitation_id' = '{}'::jsonb AND
            payload -> 'schema_version' = '1'::jsonb AND
            payload ->> 'kind' = 'responder_invitation' AND
            payload ->> 'incident_id' = incident_id::text AND
            payload ->> 'invitation_id' = invitation_id::text
        ),
        CONSTRAINT ck_notification_outbox_updated_at
            CHECK (updated_at >= created_at),
        CONSTRAINT ck_notification_outbox_next_attempt_at
            CHECK (next_attempt_at >= created_at),
        CONSTRAINT ck_notification_outbox_lease_state
            CHECK ((lease_token IS NULL) = (lease_until IS NULL)),
        CONSTRAINT ck_notification_outbox_terminal_state CHECK (
            (
                status = 'pending' AND finalized_at IS NULL AND
                provider_message_id IS NULL AND last_error_code IS NULL
            ) OR (
                status = 'provider_accepted' AND finalized_at IS NOT NULL AND
                provider_message_id = notification_id AND
                last_error_code IS NULL AND
                attempt_count >= 1
            ) OR (
                status IN ('permanent_failed', 'unavailable', 'unknown') AND
                finalized_at IS NOT NULL AND provider_message_id IS NULL AND
                last_error_code IS NOT NULL
            )
        ),
        CONSTRAINT ck_notification_outbox_error_code CHECK (
            last_error_code IS NULL OR last_error_code IN (
                'active_push_registration_unavailable', 'bad_apns_topic',
                'bad_device_token', 'device_token_not_for_topic',
                'device_token_unreadable', 'device_token_unregistered',
                'incident_not_escalating', 'invalid_apns_topic',
                'invalid_device_token', 'invitation_not_pending',
                'missing_apns_topic', 'payload_empty', 'payload_too_large',
                'provider_authentication_failed', 'provider_delayed_retry',
                'provider_outcome_unknown', 'provider_rate_limited',
                'provider_rejected', 'provider_response_invalid',
                'provider_unavailable',
                'responder_not_notification_allowlisted'
            )
        ),
        CONSTRAINT ck_notification_outbox_finalized_at
            CHECK (finalized_at IS NULL OR finalized_at >= created_at),
        CONSTRAINT ck_notification_outbox_not_simulated
            CHECK (simulated = false)
    )
    """,
    """
    CREATE INDEX ix_notification_outbox_due
    ON notification_outbox (
        scope_id, status, next_attempt_at, created_at
    )
    WHERE status = 'pending'
    """,
    """
    CREATE TABLE notification_delivery_attempts (
        scope_id UUID NOT NULL,
        attempt_id UUID NOT NULL,
        notification_id UUID NOT NULL,
        invitation_id UUID NOT NULL,
        attempt_number INTEGER NOT NULL,
        outcome VARCHAR(32) NOT NULL,
        provider_message_id UUID,
        error_code VARCHAR(64),
        requested_at TIMESTAMP WITH TIME ZONE NOT NULL,
        responded_at TIMESTAMP WITH TIME ZONE NOT NULL,
        simulated BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (scope_id, attempt_id),
        CONSTRAINT fk_notification_attempts_exact_outbox
            FOREIGN KEY (scope_id, notification_id, invitation_id)
            REFERENCES notification_outbox (
                scope_id, notification_id, invitation_id
            )
            ON DELETE CASCADE,
        CONSTRAINT uq_notification_attempts_invitation_sequence
            UNIQUE (scope_id, invitation_id, attempt_number),
        CONSTRAINT ck_notification_attempts_number
            CHECK (attempt_number >= 1),
        CONSTRAINT ck_notification_attempts_outcome CHECK (
            outcome IN (
                'provider_accepted', 'transient_failure',
                'permanent_failure', 'unknown'
            )
        ),
        CONSTRAINT ck_notification_attempts_responded_at
            CHECK (responded_at >= requested_at),
        CONSTRAINT ck_notification_attempts_outcome_metadata CHECK (
            (
                outcome = 'provider_accepted' AND
                provider_message_id = notification_id AND error_code IS NULL
            ) OR (
                outcome <> 'provider_accepted' AND
                provider_message_id IS NULL AND error_code IS NOT NULL
            )
        ),
        CONSTRAINT ck_notification_attempts_error_code CHECK (
            error_code IS NULL OR error_code IN (
                'active_push_registration_unavailable', 'bad_apns_topic',
                'bad_device_token', 'device_token_not_for_topic',
                'device_token_unreadable', 'device_token_unregistered',
                'incident_not_escalating', 'invalid_apns_topic',
                'invalid_device_token', 'invitation_not_pending',
                'missing_apns_topic', 'payload_empty', 'payload_too_large',
                'provider_authentication_failed', 'provider_delayed_retry',
                'provider_outcome_unknown', 'provider_rate_limited',
                'provider_rejected', 'provider_response_invalid',
                'provider_unavailable',
                'responder_not_notification_allowlisted'
            )
        ),
        CONSTRAINT ck_notification_attempts_not_simulated
            CHECK (simulated = false)
    )
    """,
    """
    CREATE INDEX ix_notification_attempts_scope_invitation
    ON notification_delivery_attempts (
        scope_id, invitation_id, attempt_number
    )
    """,
    """
    CREATE TRIGGER tr_notification_delivery_attempts_append_only
    BEFORE UPDATE OR DELETE ON notification_delivery_attempts
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_incident_audit_change()
    """,
)


DOWNGRADE_STATEMENTS = (
    "DROP TABLE notification_delivery_attempts",
    "DROP TABLE notification_outbox",
    "DROP TABLE responder_push_registrations",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
