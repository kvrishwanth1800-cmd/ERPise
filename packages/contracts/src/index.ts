export type HealthStatus = 'healthy' | 'unhealthy';

export interface HealthCheckResult {
  readonly service: string;
  readonly status: HealthStatus;
}

export const CONTRACT_VERSION_V1 = 'v1' as const;

export type SupportedContractVersion = typeof CONTRACT_VERSION_V1;

export type ProblemCode =
  | 'invalid_command'
  | 'unsupported_contract_version'
  | 'invalid_event';

export interface ProblemDetail {
  readonly code: ProblemCode;
  readonly message: string;
  readonly retryable: boolean;
}

export interface CommandEnvelope<TPayload> {
  readonly version: SupportedContractVersion;
  readonly commandId: string;
  readonly commandType: string;
  readonly traceId: string;
  readonly idempotencyKey: string;
  readonly payload: TPayload;
}

export interface DomainEvent<TPayload> {
  readonly version: SupportedContractVersion;
  readonly eventId: string;
  readonly eventType: string;
  readonly occurredAt: string;
  readonly traceId: string;
  readonly payload: TPayload;
}

export type ValidationResult<TValue> =
  | { readonly ok: true; readonly value: TValue }
  | { readonly ok: false; readonly problem: ProblemDetail };

export function validateCommandEnvelope(
  value: unknown
): ValidationResult<CommandEnvelope<unknown>> {
  if (!isRecord(value)) {
    return invalidCommand('A command envelope must be an object.');
  }
  if (value.version !== CONTRACT_VERSION_V1) {
    return unsupportedVersion();
  }
  for (const field of [
    'commandId',
    'commandType',
    'traceId',
    'idempotencyKey'
  ]) {
    if (!isNonEmptyString(value[field])) {
      return invalidCommand(
        `Command envelope field ${field} must be a non-empty string.`
      );
    }
  }
  return { ok: true, value: value as CommandEnvelope<unknown> };
}

export function validateDomainEvent(
  value: unknown
): ValidationResult<DomainEvent<unknown>> {
  if (!isRecord(value)) {
    return invalidEvent('A domain event must be an object.');
  }
  if (value.version !== CONTRACT_VERSION_V1) {
    return unsupportedVersion();
  }
  for (const field of ['eventId', 'eventType', 'occurredAt', 'traceId']) {
    if (!isNonEmptyString(value[field])) {
      return invalidEvent(
        `Domain event field ${field} must be a non-empty string.`
      );
    }
  }
  return { ok: true, value: value as DomainEvent<unknown> };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0;
}

function invalidCommand(message: string): ValidationResult<never> {
  return {
    ok: false,
    problem: { code: 'invalid_command', message, retryable: false }
  };
}

function invalidEvent(message: string): ValidationResult<never> {
  return {
    ok: false,
    problem: { code: 'invalid_event', message, retryable: false }
  };
}

function unsupportedVersion(): ValidationResult<never> {
  return {
    ok: false,
    problem: {
      code: 'unsupported_contract_version',
      message: 'The contract version is not supported.',
      retryable: false
    }
  };
}
