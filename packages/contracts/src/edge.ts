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
  readonly sequence: number;
  readonly retryCount: number;
  readonly command: SaleCommand;
}

export type EdgeReconciliationOutcome =
  | 'ACCEPTED'
  | 'DUPLICATE'
  | 'RETRYABLE_FAILURE'
  | 'CONTROLLED_RECOVERY';

export interface EdgeOperationReconciled {
  readonly sequence: number;
  readonly outcome: EdgeReconciliationOutcome;
  readonly diagnostic: string;
}
