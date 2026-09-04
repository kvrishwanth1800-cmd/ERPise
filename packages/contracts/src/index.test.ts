import { describe, expect, it } from 'vitest';

import type { HealthCheckResult } from './index.js';

describe('HealthCheckResult', () => {
  it('represents a healthy dependency', () => {
    const result: HealthCheckResult = { service: 'postgres', status: 'healthy' };

    expect(result.status).toBe('healthy');
  });
});
