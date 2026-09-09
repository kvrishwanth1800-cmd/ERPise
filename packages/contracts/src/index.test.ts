import { describe, expect, it } from 'vitest';

import type { EdgeOperationReconciled, EdgeSyncEnvelope } from './edge.js';
import {
  CONTRACT_VERSION_V1,
  type HealthCheckResult,
  validateCommandEnvelope,
  validateDomainEvent
} from './index.js';

describe('HealthCheckResult', () => {
  it('represents a healthy dependency', () => {
    const result: HealthCheckResult = {
      service: 'postgres',
      status: 'healthy'
    };
    expect(result.status).toBe('healthy');
  });
});

describe('versioned command contracts', () => {
  it('accepts a v1 idempotent command with trace evidence', () => {
    const result = validateCommandEnvelope({
      version: CONTRACT_VERSION_V1,
      commandId: 'command-1',
      commandType: 'organization.update',
      traceId: 'trace-1',
      idempotencyKey: 'request-1',
      payload: { organizationId: 'org-1' }
    });

    expect(result.ok).toBe(true);
  });

  it('rejects a command without an idempotency key', () => {
    const result = validateCommandEnvelope({
      version: CONTRACT_VERSION_V1,
      commandId: 'command-1',
      commandType: 'organization.update',
      traceId: 'trace-1',
      idempotencyKey: '',
      payload: {}
    });

    expect(result).toMatchObject({
      ok: false,
      problem: { code: 'invalid_command' }
    });
  });

  it('rejects an unsupported command version', () => {
    const result = validateCommandEnvelope({ version: 'v2' });

    expect(result).toMatchObject({
      ok: false,
      problem: { code: 'unsupported_contract_version' }
    });
  });
});

describe('versioned event contracts', () => {
  it('accepts a v1 event with a stable trace contract', () => {
    const result = validateDomainEvent({
      version: CONTRACT_VERSION_V1,
      eventId: 'event-1',
      eventType: 'OrganizationChanged',
      occurredAt: '2026-09-04T00:00:00Z',
      traceId: 'trace-1',
      payload: { action: 'created' }
    });

    expect(result.ok).toBe(true);
  });

  it('rejects an event that removes a required compatibility field', () => {
    const result = validateDomainEvent({
      version: CONTRACT_VERSION_V1,
      eventId: 'event-1',
      eventType: 'OrganizationChanged',
      occurredAt: '2026-09-04T00:00:00Z',
      payload: {}
    });

    expect(result).toMatchObject({
      ok: false,
      problem: { code: 'invalid_event' }
    });
  });
});

describe('edge synchronization contracts', () => {
  it('retains device scope, ordered identity, and reconciliation state', () => {
    const envelope: EdgeSyncEnvelope = {
      version: CONTRACT_VERSION_V1,
      tenantId: 'tenant-1',
      siteId: 'site-1',
      registerId: 'register-1',
      deviceId: 'device-1',
      sequence: 1,
      retryCount: 0,
      command: {
        commandId: 'sale-1',
        idempotencyKey: 'sale-request-1',
        traceId: 'trace-1',
        causationId: 'cause-1',
        correlationId: 'correlation-1',
        payload: {}
      }
    };
    const reconciled: EdgeOperationReconciled = {
      sequence: envelope.sequence,
      outcome: 'DUPLICATE',
      diagnostic: 'already committed'
    };

    expect(reconciled.outcome).toBe('DUPLICATE');
  });
});
