# WO-4 implementation plan

1. Add an application-service authorization boundary that evaluates explicit permissions against trusted tenant, organization, and record scope.
2. Add session revocation state and require authorization checks to deny revoked sessions.
3. Add conflicting-duty assignment checks for requester, approver, payer, bank-editor, and reconciler duties.
4. Add unit tests for each acceptance criterion and validate through required CI workflows.
