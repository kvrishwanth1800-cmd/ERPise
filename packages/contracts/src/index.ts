export type HealthStatus = 'healthy' | 'unhealthy';

export interface HealthCheckResult {
  readonly service: string;
  readonly status: HealthStatus;
}
