#![forbid(unsafe_code)]

//! Encrypted, device-bound, ordered local synchronization state for a store edge.

use rusqlite::{params, Connection, OptionalExtension, Transaction, TransactionBehavior};
use serde::{Deserialize, Serialize};
use std::fmt::Write;
use std::path::Path;

pub const CURRENT_SCHEMA_VERSION: i64 = 1;

#[derive(Debug)]
pub enum EdgeSyncError {
    Database(rusqlite::Error),
    Serialization(serde_json::Error),
    InvalidInput(&'static str),
    DeviceNotAuthorized,
    InvalidTransition,
    ControlledRecoveryRequired,
    SqlCipherUnavailable,
}

impl From<rusqlite::Error> for EdgeSyncError {
    fn from(value: rusqlite::Error) -> Self {
        Self::Database(value)
    }
}

impl From<serde_json::Error> for EdgeSyncError {
    fn from(value: serde_json::Error) -> Self {
        Self::Serialization(value)
    }
}

pub type Result<T> = std::result::Result<T, EdgeSyncError>;

/// An OS secure-store adapter. Implementations must never log, export, or persist the key.
pub trait SecureKeyProvider {
    fn database_key(&self, device_id: &str) -> Result<Vec<u8>>;
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct DeviceBinding {
    pub tenant_id: String,
    pub site_id: String,
    pub register_id: String,
    pub device_id: String,
    pub credential_id: String,
}

impl DeviceBinding {
    fn validate(&self) -> Result<()> {
        for value in [
            &self.tenant_id,
            &self.site_id,
            &self.register_id,
            &self.device_id,
            &self.credential_id,
        ] {
            if value.trim().is_empty() {
                return Err(EdgeSyncError::InvalidInput(
                    "device binding fields are required",
                ));
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SaleCommand {
    pub command_id: String,
    pub idempotency_key: String,
    pub trace_id: String,
    pub causation_id: String,
    pub correlation_id: String,
    pub payload: serde_json::Value,
}

impl SaleCommand {
    fn validate(&self) -> Result<()> {
        for value in [
            &self.command_id,
            &self.idempotency_key,
            &self.trace_id,
            &self.causation_id,
            &self.correlation_id,
        ] {
            if value.trim().is_empty() {
                return Err(EdgeSyncError::InvalidInput(
                    "command identity fields are required",
                ));
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct EdgeSyncEnvelope {
    pub version: String,
    pub tenant_id: String,
    pub site_id: String,
    pub register_id: String,
    pub device_id: String,
    pub sequence: i64,
    pub retry_count: i64,
    pub command: SaleCommand,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ReconciliationOutcome {
    Accepted,
    Duplicate,
    RetryableFailure,
    ControlledRecovery,
}

impl ReconciliationOutcome {
    fn as_str(self) -> &'static str {
        match self {
            Self::Accepted => "ACCEPTED",
            Self::Duplicate => "DUPLICATE",
            Self::RetryableFailure => "RETRYABLE_FAILURE",
            Self::ControlledRecovery => "CONTROLLED_RECOVERY",
        }
    }

    fn advances_cursor(self) -> bool {
        matches!(self, Self::Accepted | Self::Duplicate)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct EdgeOperationReconciled {
    pub sequence: i64,
    pub outcome: ReconciliationOutcome,
    /// A bounded, non-sensitive diagnostic code. Never include payload, keys, or credentials.
    pub diagnostic_code: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FreshnessState {
    Online,
    Offline,
    Stale,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Freshness {
    pub state: FreshnessState,
    pub last_successful_sync_at: Option<i64>,
}

pub struct EdgeStore {
    connection: Connection,
}

impl EdgeStore {
    pub fn open_encrypted(
        path: &Path,
        key_provider: &dyn SecureKeyProvider,
        device_id: &str,
    ) -> Result<Self> {
        if device_id.trim().is_empty() {
            return Err(EdgeSyncError::InvalidInput("device id is required"));
        }
        let key = key_provider.database_key(device_id)?;
        if key.len() < 32 {
            return Err(EdgeSyncError::InvalidInput(
                "secure-store key must be at least 32 bytes",
            ));
        }

        let mut hex_key = String::with_capacity(key.len() * 2);
        for byte in key {
            write!(&mut hex_key, "{byte:02x}")
                .map_err(|_| EdgeSyncError::InvalidInput("key encoding failed"))?;
        }

        let connection = Connection::open(path)?;
        connection.execute_batch(&format!(
            "PRAGMA key = \"x'{hex_key}'\"; PRAGMA cipher_memory_security = ON; PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL; PRAGMA synchronous = FULL;"
        ))?;
        let cipher_version: Option<String> = connection
            .query_row("PRAGMA cipher_version", [], |row| row.get(0))
            .optional()?;
        if cipher_version.is_none_or(|value| value.trim().is_empty()) {
            return Err(EdgeSyncError::SqlCipherUnavailable);
        }

        let store = Self { connection };
        store.migrate()?;
        Ok(store)
    }

    pub fn migrate(&self) -> Result<()> {
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        transaction.execute_batch(
            "CREATE TABLE IF NOT EXISTS edge_schema_version (version INTEGER NOT NULL);
             CREATE TABLE IF NOT EXISTS device_binding (
                device_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                site_id TEXT NOT NULL,
                register_id TEXT NOT NULL,
                credential_id TEXT NOT NULL,
                revoked INTEGER NOT NULL CHECK (revoked IN (0, 1))
             );
             CREATE TABLE IF NOT EXISTS edge_operations (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                command_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL UNIQUE,
                trace_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('PENDING', 'RECONCILED', 'CONTROLLED_RECOVERY')),
                retry_count INTEGER NOT NULL CHECK (retry_count >= 0),
                next_attempt_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL
             );
             CREATE TABLE IF NOT EXISTS edge_cursor (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                sequence INTEGER NOT NULL CHECK (sequence >= 0),
                last_successful_sync_at INTEGER
             );
             CREATE TABLE IF NOT EXISTS edge_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL,
                occurred_at INTEGER NOT NULL
             );",
        )?;
        let version: Option<i64> = transaction
            .query_row("SELECT version FROM edge_schema_version", [], |row| row.get(0))
            .optional()?;
        match version {
            None => {
                transaction.execute(
                    "INSERT INTO edge_schema_version (version) VALUES (?1)",
                    [CURRENT_SCHEMA_VERSION],
                )?;
            }
            Some(CURRENT_SCHEMA_VERSION) => {}
            Some(_) => return Err(EdgeSyncError::ControlledRecoveryRequired),
        }
        transaction.execute(
            "INSERT OR IGNORE INTO edge_cursor (singleton, sequence, last_successful_sync_at) VALUES (1, 0, NULL)",
            [],
        )?;
        transaction.commit()?;
        Ok(())
    }

    /// Downgrade is intentionally refused. It would destroy encrypted evidence and queue state.
    pub fn rollback_migration(&self) -> Result<()> {
        Err(EdgeSyncError::ControlledRecoveryRequired)
    }

    /// Initial enrollment is insert-only. Credential rotation requires controlled re-enrollment.
    pub fn enroll(&self, binding: &DeviceBinding, occurred_at: i64) -> Result<()> {
        binding.validate()?;
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        transaction.execute(
            "INSERT INTO device_binding (device_id, tenant_id, site_id, register_id, credential_id, revoked)
             VALUES (?1, ?2, ?3, ?4, ?5, 0)",
            params![
                binding.device_id,
                binding.tenant_id,
                binding.site_id,
                binding.register_id,
                binding.credential_id,
            ],
        )?;
        audit(&transaction, "device.enrolled", &binding.device_id, occurred_at)?;
        transaction.commit()?;
        Ok(())
    }

    pub fn revoke(&self, device_id: &str, occurred_at: i64) -> Result<()> {
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let changed = transaction.execute(
            "UPDATE device_binding SET revoked = 1 WHERE device_id = ?1 AND revoked = 0",
            [device_id],
        )?;
        if changed != 1 {
            return Err(EdgeSyncError::DeviceNotAuthorized);
        }
        audit(&transaction, "device.revoked", device_id, occurred_at)?;
        transaction.commit()?;
        Ok(())
    }

    pub fn queue_sale(
        &self,
        binding: &DeviceBinding,
        command: &SaleCommand,
        now: i64,
    ) -> Result<i64> {
        self.authorize(binding)?;
        command.validate()?;
        let payload = serde_json::to_string(command)?;
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        transaction.execute(
            "INSERT INTO edge_operations (command_id, idempotency_key, trace_id, payload, state, retry_count, next_attempt_at, created_at)
             VALUES (?1, ?2, ?3, ?4, 'PENDING', 0, ?5, ?5)",
            params![
                command.command_id,
                command.idempotency_key,
                command.trace_id,
                payload,
                now,
            ],
        )?;
        let sequence = transaction.last_insert_rowid();
        audit(&transaction, "operation.queued", &command.trace_id, now)?;
        transaction.commit()?;
        Ok(sequence)
    }

    /// Returns only the outbox head. A delayed or recovery head blocks later operations.
    pub fn next_pending(
        &self,
        binding: &DeviceBinding,
        now: i64,
    ) -> Result<Option<EdgeSyncEnvelope>> {
        self.authorize(binding)?;
        let row: Option<(i64, i64, i64, String, String)> = self
            .connection
            .query_row(
                "SELECT sequence, retry_count, next_attempt_at, state, payload
                 FROM edge_operations
                 WHERE sequence > (SELECT sequence FROM edge_cursor WHERE singleton = 1)
                 ORDER BY sequence LIMIT 1",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?)),
            )
            .optional()?;
        let Some((sequence, retry_count, next_attempt_at, state, payload)) = row else {
            return Ok(None);
        };
        if state != "PENDING" || next_attempt_at > now {
            return Ok(None);
        }
        let command = serde_json::from_str(&payload)?;
        Ok(Some(EdgeSyncEnvelope {
            version: "v1".to_owned(),
            tenant_id: binding.tenant_id.clone(),
            site_id: binding.site_id.clone(),
            register_id: binding.register_id.clone(),
            device_id: binding.device_id.clone(),
            sequence,
            retry_count,
            command,
        }))
    }

    pub fn reconcile(
        &self,
        binding: &DeviceBinding,
        result: &EdgeOperationReconciled,
        now: i64,
        retry_limit: i64,
        retry_cap_seconds: i64,
    ) -> Result<()> {
        self.authorize(binding)?;
        if result.sequence <= 0 || retry_limit < 0 || retry_cap_seconds < 1 {
            return Err(EdgeSyncError::InvalidInput("invalid reconciliation configuration"));
        }
        if result.diagnostic_code.trim().is_empty() || result.diagnostic_code.len() > 128 {
            return Err(EdgeSyncError::InvalidInput("invalid diagnostic code"));
        }

        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let cursor: i64 = transaction.query_row(
            "SELECT sequence FROM edge_cursor WHERE singleton = 1",
            [],
            |row| row.get(0),
        )?;
        let (state, retry_count, trace_id): (String, i64, String) = transaction.query_row(
            "SELECT state, retry_count, trace_id FROM edge_operations WHERE sequence = ?1",
            [result.sequence],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )?;

        if result.outcome.advances_cursor() {
            if result.sequence == cursor && state == "RECONCILED" {
                return Ok(());
            }
            if result.sequence != cursor + 1 || state != "PENDING" {
                return Err(EdgeSyncError::InvalidTransition);
            }
            transaction.execute(
                "UPDATE edge_operations SET state = 'RECONCILED' WHERE sequence = ?1",
                [result.sequence],
            )?;
            transaction.execute(
                "UPDATE edge_cursor SET sequence = ?1, last_successful_sync_at = ?2 WHERE singleton = 1",
                params![result.sequence, now],
            )?;
        } else if result.outcome == ReconciliationOutcome::RetryableFailure {
            if result.sequence != cursor + 1 || state != "PENDING" {
                return Err(EdgeSyncError::InvalidTransition);
            }
            let next_count = retry_count
                .checked_add(1)
                .ok_or(EdgeSyncError::ControlledRecoveryRequired)?;
            if next_count > retry_limit {
                transaction.execute(
                    "UPDATE edge_operations SET state = 'CONTROLLED_RECOVERY', retry_count = ?2 WHERE sequence = ?1",
                    params![result.sequence, next_count],
                )?;
            } else {
                let delay = 1_i64
                    .checked_shl(next_count.min(30) as u32)
                    .unwrap_or(i64::MAX)
                    .min(retry_cap_seconds);
                let next_attempt = now
                    .checked_add(delay)
                    .ok_or(EdgeSyncError::ControlledRecoveryRequired)?;
                transaction.execute(
                    "UPDATE edge_operations SET retry_count = ?2, next_attempt_at = ?3 WHERE sequence = ?1",
                    params![result.sequence, next_count, next_attempt],
                )?;
            }
        } else {
            if result.sequence != cursor + 1 || state != "PENDING" {
                return Err(EdgeSyncError::InvalidTransition);
            }
            transaction.execute(
                "UPDATE edge_operations SET state = 'CONTROLLED_RECOVERY' WHERE sequence = ?1",
                [result.sequence],
            )?;
        }
        audit(
            &transaction,
            &format!("operation.{}", result.outcome.as_str()),
            &trace_id,
            now,
        )?;
        transaction.commit()?;
        Ok(())
    }

    pub fn freshness(&self, connected: bool, now: i64, stale_after_seconds: i64) -> Result<Freshness> {
        if stale_after_seconds < 0 {
            return Err(EdgeSyncError::InvalidInput("stale threshold must be non-negative"));
        }
        let last: Option<i64> = self.connection.query_row(
            "SELECT last_successful_sync_at FROM edge_cursor WHERE singleton = 1",
            [],
            |row| row.get(0),
        )?;
        let state = if !connected {
            FreshnessState::Offline
        } else if last.is_none_or(|value| value > now || now - value > stale_after_seconds) {
            FreshnessState::Stale
        } else {
            FreshnessState::Online
        };
        Ok(Freshness {
            state,
            last_successful_sync_at: last,
        })
    }

    fn authorize(&self, binding: &DeviceBinding) -> Result<()> {
        binding.validate()?;
        let row: Option<(String, String, String, String, i64)> = self
            .connection
            .query_row(
                "SELECT tenant_id, site_id, register_id, credential_id, revoked
                 FROM device_binding WHERE device_id = ?1",
                [&binding.device_id],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?)),
            )
            .optional()?;
        match row {
            Some((tenant, site, register, credential, revoked))
                if revoked == 0
                    && tenant == binding.tenant_id
                    && site == binding.site_id
                    && register == binding.register_id
                    && credential == binding.credential_id => Ok(()),
            _ => Err(EdgeSyncError::DeviceNotAuthorized),
        }
    }
}

fn audit(transaction: &Transaction<'_>, action: &str, trace_id: &str, occurred_at: i64) -> Result<()> {
    transaction.execute(
        "INSERT INTO edge_audit (trace_id, action, details, occurred_at) VALUES (?1, ?2, ?3, ?4)",
        params![trace_id, action, "{\"redacted\":true}", occurred_at],
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::NamedTempFile;

    struct TestKeys(u8);

    impl SecureKeyProvider for TestKeys {
        fn database_key(&self, _: &str) -> Result<Vec<u8>> {
            Ok(vec![self.0; 32])
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
        let store = EdgeStore::open_encrypted(file.path(), &TestKeys(7), "device-a").unwrap();
        store.enroll(&binding(), 1).unwrap();
        (file, store)
    }

    #[test]
    fn reopens_only_with_the_same_key() {
        let (file, store) = store();
        store.queue_sale(&binding(), &command("one"), 1).unwrap();
        drop(store);
        assert!(EdgeStore::open_encrypted(file.path(), &TestKeys(7), "device-a").is_ok());
        assert!(EdgeStore::open_encrypted(file.path(), &TestKeys(8), "device-a").is_err());
    }

    #[test]
    fn delayed_head_blocks_later_operations() {
        let (_, store) = store();
        let first = store.queue_sale(&binding(), &command("one"), 1).unwrap();
        store.queue_sale(&binding(), &command("two"), 1).unwrap();
        store
            .reconcile(
                &binding(),
                &EdgeOperationReconciled {
                    sequence: first,
                    outcome: ReconciliationOutcome::RetryableFailure,
                    diagnostic_code: "transport_timeout".into(),
                },
                1,
                3,
                60,
            )
            .unwrap();
        assert!(store.next_pending(&binding(), 2).unwrap().is_none());
    }

    #[test]
    fn cursor_rejects_out_of_order_reconciliation() {
        let (_, store) = store();
        store.queue_sale(&binding(), &command("one"), 1).unwrap();
        let second = store.queue_sale(&binding(), &command("two"), 1).unwrap();
        let error = store
            .reconcile(
                &binding(),
                &EdgeOperationReconciled {
                    sequence: second,
                    outcome: ReconciliationOutcome::Accepted,
                    diagnostic_code: "accepted".into(),
                },
                2,
                3,
                60,
            )
            .unwrap_err();
        assert!(matches!(error, EdgeSyncError::InvalidTransition));
    }

    #[test]
    fn revoked_or_mismatched_binding_blocks_work() {
        let (_, store) = store();
        let mut mismatched = binding();
        mismatched.register_id = "register-b".into();
        assert!(matches!(
            store.queue_sale(&mismatched, &command("one"), 1),
            Err(EdgeSyncError::DeviceNotAuthorized)
        ));
        store.revoke("device-a", 2).unwrap();
        assert!(matches!(
            store.queue_sale(&binding(), &command("one"), 2),
            Err(EdgeSyncError::DeviceNotAuthorized)
        ));
    }
}
