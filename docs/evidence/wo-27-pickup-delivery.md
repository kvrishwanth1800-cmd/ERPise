# WO-27 Pickup and Delivery Evidence

The fulfillment service reuses scoped authorization context, append-only audit records, and outbox event patterns. It offers capacity-backed pickup and delivery promises, validates delivery service areas, supports pickup readiness and collection, and controls delivery assignment, dispatch, proof, failure recovery, reservation release, and payment refund linkage.

Focused tests cover delivery proof, idempotent promise reuse, tenant isolation, service-area rejection, capacity usage, and cancellation/refund recovery. The PostgreSQL migration has a down section that drops its only created table.
