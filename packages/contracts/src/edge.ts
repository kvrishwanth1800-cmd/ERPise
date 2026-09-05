import type { SupportedContractVersion } from './index.js';

export interface SaleCommand {
  readonly commandId: string;
  readonly idempotencyKey: string;
  readonly traceId: string;
  readonly causationId: string;
  readonly correlationId: string;
  readonly payload: Record<string, unknown>;
}

export interface EdgeSyncEnvelope {
  readonly version: SupportedContractVersion;
  readonly tenantId: string;
  readonly siteId: string;
  readonly registerId: string;
  readonly deviceId: string;
  readonly sequence: string;
  readonly retryCount: number;
  readonly command: SaleCommand;
}

export type EdgeReconciliationOutcome =
  | 'ACCEPTED'
  | 'DUPLICATE'
  | 'RETRYABLE_FAILURE'
  | 'CONTROLLED_RECOVERY';

export interface EdgeOperationReconciled {
  readonly tenantId: string;
  readonly siteId: string;
  readonly registerId: string;
  readonly deviceId: string;
  readonly sequence: string;
  readonly serverCursor: string;
  readonly outcome: EdgeReconciliationOutcome;
  readonly diagnosticCode: string;
}

export function isEdgeReconciliationOutcome(
  value: unknown
): value is EdgeReconciliationOutcome {
  return (
    value === 'ACCEPTED' ||
    value === 'DUPLICATE' ||
    value === 'RETRYABLE_FAILURE' ||
    value === 'CONTROLLED_RECOVERY'
  );
}

export function isEdgeSequence(value: unknown): value is string {
  return typeof value === 'string' && /^(0|[1-9][0-9]*)$/.test(value);
}
