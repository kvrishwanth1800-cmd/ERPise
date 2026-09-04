import { describe, expect, it } from 'vitest';

import {
  CONTRACT_VERSION_V1,
  type HealthCheckResult,
  validateCommandEnvelope,
  validateDomainEvent,
} from './index.js';

describe('HealthCheckResult', () => {
  it('represents a healthy dependency', () => {
    const result: HealthCheckResult = {
      service: 'postgres',
      status: 'healthy',
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
      payload: { organizationId: 'org-1' },
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
      payload: {},
    });

    expect(result).toMatchObject({
      ok: false,
      problem: { code: 'invalid_command' },
    });
  });

  it('rejects an unsupported command version', () => {
    const result = validateCommandEnvelope({ version: 'v2' });

    expect(result).toMatchObject({
      ok: false,
      problem: { code: 'unsupported_contract_version' },
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
      payload: { action: 'created' },
    });

    expect(result.ok).toBe(true);
  });

  it('rejects an event that removes a required compatibility field', () => {
    const result = validateDomainEvent({
      version: CONTRACT_VERSION_V1,
      eventId: 'event-1',
      eventType: 'OrganizationChanged',
      occurredAt: '2026-09-04T00:00:00Z',
      payload: {},
    });

    expect(result).toMatchObject({
      ok: false,
      problem: { code: 'invalid_event' },
    });
  });
});
