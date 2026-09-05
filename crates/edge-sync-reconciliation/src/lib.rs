#![forbid(unsafe_code)]

//! Reconnect orchestration for the encrypted edge store.
//!
//! The client is injected. This crate does not choose an HTTP transport,
//! persist credentials, or implement central business effects.

use edge_sync::{
    DeviceBinding, EdgeOperationReconciled, EdgeStore, EdgeSyncEnvelope, ReconciliationOutcome,
    Result,
};

/// A BFF-facing client. Implementations send exactly the supplied envelope and
/// return the BFF's bounded reconciliation outcome.
pub trait BffReconciliationClient {
    fn reconcile(
        &mut self,
        envelope: &EdgeSyncEnvelope,
    ) -> std::result::Result<EdgeOperationReconciled, BffReconciliationError>;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BffReconciliationError {
    /// The BFF was not reachable. The operation remains durable and is retried
    /// by the normal ordered backoff path.
    Unavailable,
}

/// Applies one eligible local operation after connectivity returns.
pub struct EdgeSynchronizationController<'a, Client> {
    store: &'a mut EdgeStore,
    client: &'a mut Client,
    retry_limit: i64,
    retry_cap_seconds: i64,
}

impl<'a, Client: BffReconciliationClient> EdgeSynchronizationController<'a, Client> {
    pub fn new(
        store: &'a mut EdgeStore,
        client: &'a mut Client,
        retry_limit: i64,
        retry_cap_seconds: i64,
    ) -> Self {
        Self {
            store,
            client,
            retry_limit,
            retry_cap_seconds,
        }
    }

    /// Sends the next ordered operation at most once. `Ok(false)` means that
    /// there is no operation eligible to dispatch at `now`.
    pub fn synchronize_once(&mut self, binding: &DeviceBinding, now: i64) -> Result<bool> {
        let Some(envelope) = self.store.next_pending(binding, now)? else {
            return Ok(false);
        };
        let result = match self.client.reconcile(&envelope) {
            Ok(result) if result.sequence == envelope.sequence => result,
            Ok(_) => EdgeOperationReconciled {
                sequence: envelope.sequence,
                outcome: ReconciliationOutcome::ControlledRecovery,
                diagnostic_code: "bff_sequence_mismatch".to_owned(),
            },
            Err(BffReconciliationError::Unavailable) => EdgeOperationReconciled {
                sequence: envelope.sequence,
                outcome: ReconciliationOutcome::RetryableFailure,
                diagnostic_code: "bff_unavailable".to_owned(),
            },
        };
        self.store.reconcile(
            binding,
            &result,
            now,
            self.retry_limit,
            self.retry_cap_seconds,
        )?;
        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use edge_sync::{EdgeSyncError, FreshnessState, SaleCommand, SecureKeyProvider};
    use std::collections::VecDeque;
    use tempfile::NamedTempFile;

    struct TestKeys;
    impl SecureKeyProvider for TestKeys {
        fn database_key(&self, _: &str) -> Result<Vec<u8>> {
            Ok(vec![7; 32])
        }
    }

    fn binding() -> DeviceBinding {
        DeviceBinding {
            tenant_id: "tenant-a".into(),
            site_id: "site-a".into(),
            register_id: "register-a".into(),
            device_id: "device-a".into(),
            credential_id: "credential-a".into(),
        }
    }

    fn command(id: &str) -> SaleCommand {
        SaleCommand {
            command_id: id.into(),
            idempotency_key: format!("key-{id}"),
            trace_id: format!("trace-{id}"),
            causation_id: "cause".into(),
            correlation_id: "correlation".into(),
            payload: serde_json::json!({ "sale": id }),
        }
    }

    fn store() -> (NamedTempFile, EdgeStore) {
        let file = NamedTempFile::new().unwrap();
        let mut store = EdgeStore::open_encrypted(file.path(), &TestKeys, "device-a").unwrap();
        store.enroll(&binding(), 1).unwrap();
        (file, store)
    }

    struct FakeBff {
        outcomes: VecDeque<std::result::Result<EdgeOperationReconciled, BffReconciliationError>>,
        sent: Vec<EdgeSyncEnvelope>,
    }

    impl BffReconciliationClient for FakeBff {
        fn reconcile(
            &mut self,
            envelope: &EdgeSyncEnvelope,
        ) -> std::result::Result<EdgeOperationReconciled, BffReconciliationError> {
            self.sent.push(envelope.clone());
            self.outcomes.pop_front().unwrap()
        }
    }

    #[test]
    fn reconnect_dispatches_ordered_operations_and_accepts_duplicates() {
        let (_, mut store) = store();
        let first = store.queue_sale(&binding(), &command("one"), 1).unwrap();
        let second = store.queue_sale(&binding(), &command("two"), 1).unwrap();
        let mut bff = FakeBff {
            outcomes: VecDeque::from([
                Ok(EdgeOperationReconciled {
                    sequence: first,
                    outcome: ReconciliationOutcome::Duplicate,
                    diagnostic_code: "central_duplicate".into(),
                }),
                Ok(EdgeOperationReconciled {
                    sequence: second,
                    outcome: ReconciliationOutcome::Accepted,
                    diagnostic_code: "accepted".into(),
                }),
            ]),
            sent: Vec::new(),
        };
        let mut controller = EdgeSynchronizationController::new(&mut store, &mut bff, 2, 60);

        assert!(controller.synchronize_once(&binding(), 10).unwrap());
        assert!(controller.synchronize_once(&binding(), 11).unwrap());
        assert!(!controller.synchronize_once(&binding(), 12).unwrap());
        drop(controller);

        assert_eq!(bff.sent.iter().map(|item| item.sequence).collect::<Vec<_>>(), vec![first, second]);
        assert_eq!(store.freshness(true, 12, 60).unwrap().state, FreshnessState::Online);
    }

    #[test]
    fn unavailable_bff_reschedules_and_survives_restart() {
        let (file, mut store) = store();
        let sequence = store.queue_sale(&binding(), &command("one"), 1).unwrap();
        let mut bff = FakeBff {
            outcomes: VecDeque::from([Err(BffReconciliationError::Unavailable)]),
            sent: Vec::new(),
        };
        EdgeSynchronizationController::new(&mut store, &mut bff, 2, 60)
            .synchronize_once(&binding(), 10)
            .unwrap();
        assert!(store.next_pending(&binding(), 11).unwrap().is_none());
        drop(store);

        let store = EdgeStore::open_encrypted(file.path(), &TestKeys, "device-a").unwrap();
        assert_eq!(store.next_pending(&binding(), 12).unwrap().unwrap().sequence, sequence);
    }

    #[test]
    fn unresolvable_bff_response_enters_controlled_recovery() {
        let (_, mut store) = store();
        let sequence = store.queue_sale(&binding(), &command("one"), 1).unwrap();
        let mut bff = FakeBff {
            outcomes: VecDeque::from([Ok(EdgeOperationReconciled {
                sequence: sequence + 1,
                outcome: ReconciliationOutcome::Accepted,
                diagnostic_code: "wrong_sequence".into(),
            })]),
            sent: Vec::new(),
        };
        EdgeSynchronizationController::new(&mut store, &mut bff, 2, 60)
            .synchronize_once(&binding(), 10)
            .unwrap();
        assert!(store.next_pending(&binding(), 10).unwrap().is_none());
    }

    #[test]
    fn mismatched_scope_never_dispatches_to_bff() {
        let (_, mut store) = store();
        store.queue_sale(&binding(), &command("one"), 1).unwrap();
        let mut bff = FakeBff {
            outcomes: VecDeque::new(),
            sent: Vec::new(),
        };
        let mut mismatched = binding();
        mismatched.tenant_id = "tenant-b".into();
        let error = EdgeSynchronizationController::new(&mut store, &mut bff, 2, 60)
            .synchronize_once(&mismatched, 10)
            .unwrap_err();
        assert!(matches!(error, EdgeSyncError::DeviceNotAuthorized));
        assert!(bff.sent.is_empty());
    }
}
