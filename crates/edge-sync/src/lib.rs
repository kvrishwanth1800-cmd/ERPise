#![forbid(unsafe_code)]

//! Encrypted, device-bound, ordered local synchronization state for a store edge.

use rusqlite::{params, Connection, OptionalExtension};
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
    ControlledRecoveryRequired,
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

/// Platform adapters return a random, device-bound key from the OS secure store.
/// Production adapters must not log, export, or persist this value outside the store.
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
    pub revoked: bool,
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
                return Err(EdgeSyncError::InvalidInput("device binding fields are required"));
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
    pub diagnostic: String,
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
    pub fn open_encrypted(path: &Path, key_provider: &dyn SecureKeyProvider, device_id: &str) -> Result<Self> {
        if device_id.trim().is_empty() {
            return Err(EdgeSyncError::InvalidInput("device id is required"));
        }
        let key = key_provider.database_key(device_id)?;
        if key.len() < 32 {
            return Err(EdgeSyncError::InvalidInput("secure-store key must be at least 32 bytes"));
        }
        let connection = Connection::open(path)?;
        let mut hex_key = String::with_capacity(key.len() * 2);
        for byte in key {
            write!(&mut hex_key, "{byte:02x}").map_err(|_| EdgeSyncError::InvalidInput("key encoding failed"))?;
        }
        connection.execute_batch(&format!("PRAGMA key = \"x'{hex_key}'\"; PRAGMA cipher_memory_security = ON;"))?;
        let store = Self { connection };
        store.migrate()?;
        Ok(store)
    }

    pub fn migrate(&self) -> Result<()> {
        self.connection.execute_batch(
            "BEGIN;
             CREATE TABLE IF NOT EXISTS edge_schema_version (version INTEGER NOT NULL);
             CREATE TABLE IF NOT EXISTS device_binding (
               device_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, site_id TEXT NOT NULL,
               register_id TEXT NOT NULL, credential_id TEXT NOT NULL, revoked INTEGER NOT NULL
             );
             CREATE TABLE IF NOT EXISTS edge_operations (
               sequence INTEGER PRIMARY KEY AUTOINCREMENT, command_id TEXT NOT NULL UNIQUE,
               idempotency_key TEXT NOT NULL UNIQUE, trace_id TEXT NOT NULL, causation_id TEXT NOT NULL,
               correlation_id TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
               retry_count INTEGER NOT NULL, next_attempt_at INTEGER NOT NULL, created_at INTEGER NOT NULL
             );
             CREATE TABLE IF NOT EXISTS edge_cursor (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), sequence INTEGER NOT NULL, last_successful_sync_at INTEGER);
             CREATE TABLE IF NOT EXISTS edge_audit (
               audit_id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT NOT NULL, action TEXT NOT NULL,
               details TEXT NOT NULL, occurred_at INTEGER NOT NULL
             );
             INSERT INTO edge_schema_version (version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM edge_schema_version);
             INSERT INTO edge_cursor (singleton, sequence, last_successful_sync_at) SELECT 1, 0, NULL WHERE NOT EXISTS (SELECT 1 FROM edge_cursor WHERE singleton = 1);
             COMMIT;",
        )?;
        Ok(())
    }

    /// Refuses destructive rollback while pending or recovery records exist.
    pub fn rollback_migration(&self) -> Result<()> {
        let records: i64 = self.connection.query_row(
            "SELECT COUNT(*) FROM edge_operations WHERE state != 'RECONCILED'", [], |row| row.get(0),
        )?;
        if records > 0 {
            return Err(EdgeSyncError::ControlledRecoveryRequired);
        }
        self.connection.execute_batch(
            "BEGIN; DROP TABLE edge_audit; DROP TABLE edge_cursor; DROP TABLE edge_operations; DROP TABLE device_binding; DROP TABLE edge_schema_version; COMMIT;",
        )?;
        Ok(())
    }

    pub fn enroll(&self, binding: &DeviceBinding, occurred_at: i64) -> Result<()> {
        binding.validate()?;
        self.connection.execute(
            "INSERT OR REPLACE INTO device_binding (device_id, tenant_id, site_id, register_id, credential_id, revoked) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![binding.device_id, binding.tenant_id, binding.site_id, binding.register_id, binding.credential_id, binding.revoked as i64],
        )?;
        self.audit("device.enrolled", &binding.device_id, occurred_at)?;
        Ok(())
    }

    pub fn revoke(&self, device_id: &str, occurred_at: i64) -> Result<()> {
        self.connection.execute("UPDATE device_binding SET revoked = 1 WHERE device_id = ?1", [device_id])?;
        self.audit("device.revoked", device_id, occurred_at)?;
        Ok(())
    }

    pub fn queue_sale(&self, binding: &DeviceBinding, command: &SaleCommand, now: i64) -> Result<i64> {
        self.authorize(binding)?;
        for value in [&command.command_id, &command.idempotency_key, &command.trace_id, &command.causation_id, &command.correlation_id] {
            if value.trim().is_empty() {
                return Err(EdgeSyncError::InvalidInput("command identity fields are required"));
            }
        }
        let payload = serde_json::to_string(command)?;
        self.connection.execute(
            "INSERT INTO edge_operations (command_id, idempotency_key, trace_id, causation_id, correlation_id, payload, state, retry_count, next_attempt_at, created_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'PENDING', 0, ?7, ?7)",
            params![command.command_id, command.idempotency_key, command.trace_id, command.causation_id, command.correlation_id, payload, now],
        )?;
        let sequence = self.connection.last_insert_rowid();
        self.audit("operation.queued", &command.trace_id, now)?;
        Ok(sequence)
    }

    pub fn next_pending(&self, binding: &DeviceBinding, now: i64) -> Result<Option<EdgeSyncEnvelope>> {
        self.authorize(binding)?;
        self.connection.query_row(
            "SELECT sequence, retry_count, payload FROM edge_operations WHERE state = 'PENDING' AND next_attempt_at <= ?1 ORDER BY sequence LIMIT 1",
            [now],
            |row| {
                let command: SaleCommand = serde_json::from_str(&row.get::<_, String>(2)?).map_err(|err| rusqlite::Error::FromSqlConversionFailure(2, rusqlite::types::Type::Text, Box::new(err)))?;
                Ok(EdgeSyncEnvelope { version: "v1".to_owned(), tenant_id: binding.tenant_id.clone(), site_id: binding.site_id.clone(), register_id: binding.register_id.clone(), device_id: binding.device_id.clone(), sequence: row.get(0)?, retry_count: row.get(1)?, command })
            },
        ).optional().map_err(Into::into)
    }

    pub fn reconcile(&self, binding: &DeviceBinding, result: &EdgeOperationReconciled, now: i64, retry_limit: i64, retry_cap_seconds: i64) -> Result<()> {
        self.authorize(binding)?;
        let trace_id: String = self.connection.query_row("SELECT trace_id FROM edge_operations WHERE sequence = ?1", [result.sequence], |row| row.get(0))?;
        match result.outcome {
            outcome if outcome.advances_cursor() => {
                self.connection.execute("UPDATE edge_operations SET state = 'RECONCILED' WHERE sequence = ?1", [result.sequence])?;
                self.connection.execute("UPDATE edge_cursor SET sequence = ?1, last_successful_sync_at = ?2 WHERE singleton = 1", params![result.sequence, now])?;
            }
            ReconciliationOutcome::RetryableFailure => {
                let retry_count: i64 = self.connection.query_row("SELECT retry_count FROM edge_operations WHERE sequence = ?1", [result.sequence], |row| row.get(0))?;
                let next_count = retry_count + 1;
                if next_count > retry_limit {
                    self.connection.execute("UPDATE edge_operations SET state = 'CONTROLLED_RECOVERY', retry_count = ?2 WHERE sequence = ?1", params![result.sequence, next_count])?;
                } else {
                    let power = 1_i64.checked_shl(next_count.min(30) as u32).unwrap_or(i64::MAX);
                    let delay = power.min(retry_cap_seconds.max(1));
                    self.connection.execute("UPDATE edge_operations SET retry_count = ?2, next_attempt_at = ?3 WHERE sequence = ?1", params![result.sequence, next_count, now + delay])?;
                }
            }
            ReconciliationOutcome::ControlledRecovery => {
                self.connection.execute("UPDATE edge_operations SET state = 'CONTROLLED_RECOVERY' WHERE sequence = ?1", [result.sequence])?;
            }
        }
        self.audit(&format!("operation.{}", result.outcome.as_str()), &trace_id, now)?;
        Ok(())
    }

    pub fn freshness(&self, connected: bool, now: i64, stale_after_seconds: i64) -> Result<Freshness> {
        let last: Option<i64> = self.connection.query_row("SELECT last_successful_sync_at FROM edge_cursor WHERE singleton = 1", [], |row| row.get(0))?;
        let state = if !connected { FreshnessState::Offline } else if last.is_none_or(|value| now - value > stale_after_seconds) { FreshnessState::Stale } else { FreshnessState::Online };
        Ok(Freshness { state, last_successful_sync_at: last })
    }

    fn authorize(&self, binding: &DeviceBinding) -> Result<()> {
        binding.validate()?;
        let allowed: Option<(String, String, String, String, i64)> = self.connection.query_row(
            "SELECT tenant_id, site_id, register_id, credential_id, revoked FROM device_binding WHERE device_id = ?1", [binding.device_id.as_str()],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?)),
        ).optional()?;
        match allowed {
            Some((tenant, site, register, credential, revoked)) if revoked == 0 && tenant == binding.tenant_id && site == binding.site_id && register == binding.register_id && credential == binding.credential_id => Ok(()),
            _ => Err(EdgeSyncError::DeviceNotAuthorized),
        }
    }

    fn audit(&self, action: &str, trace_id: &str, occurred_at: i64) -> Result<()> {
        self.connection.execute("INSERT INTO edge_audit (trace_id, action, details, occurred_at) VALUES (?1, ?2, ?3, ?4)", params![trace_id, action, "{\"redacted\":true}", occurred_at])?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::NamedTempFile;

    struct TestKeys;
    impl SecureKeyProvider for TestKeys {
        fn database_key(&self, _: &str) -> Result<Vec<u8>> { Ok(vec![7; 32]) }
    }

    fn binding() -> DeviceBinding { DeviceBinding { tenant_id: "tenant-a".into(), site_id: "site-a".into(), register_id: "register-a".into(), device_id: "device-a".into(), credential_id: "credential-a".into(), revoked: false } }
    fn command(id: &str) -> SaleCommand { SaleCommand { command_id: id.into(), idempotency_key: format!("key-{id}"), trace_id: format!("trace-{id}"), causation_id: "cause-1".into(), correlation_id: "correlation-1".into(), payload: serde_json::json!({"sale": id}) } }
    fn store() -> (NamedTempFile, EdgeStore) { let file = NamedTempFile::new().unwrap(); let store = EdgeStore::open_encrypted(file.path(), &TestKeys, "device-a").unwrap(); store.enroll(&binding(), 1).unwrap(); (file, store) }

    #[test]
    fn encrypted_database_reopens_with_device_key() { let (file, store) = store(); store.queue_sale(&binding(), &command("one"), 1).unwrap(); drop(store); let reopened = EdgeStore::open_encrypted(file.path(), &TestKeys, "device-a").unwrap(); assert_eq!(reopened.next_pending(&binding(), 1).unwrap().unwrap().sequence, 1); }
    #[test]
    fn revoked_device_cannot_queue_or_sync() { let (_, store) = store(); store.revoke("device-a", 2).unwrap(); assert!(matches!(store.queue_sale(&binding(), &command("one"), 2), Err(EdgeSyncError::DeviceNotAuthorized))); }
    #[test]
    fn duplicate_advances_cursor_but_retry_does_not() { let (_, store) = store(); let sequence = store.queue_sale(&binding(), &command("one"), 1).unwrap(); store.reconcile(&binding(), &EdgeOperationReconciled { sequence, outcome: ReconciliationOutcome::RetryableFailure, diagnostic: "temporary".into() }, 2, 3, 10).unwrap(); assert!(store.next_pending(&binding(), 2).unwrap().is_none()); store.reconcile(&binding(), &EdgeOperationReconciled { sequence, outcome: ReconciliationOutcome::Duplicate, diagnostic: "seen".into() }, 3, 3, 10).unwrap(); assert_eq!(store.freshness(true, 3, 10).unwrap().state, FreshnessState::Online); }
    #[test]
    fn retry_exhaustion_preserves_controlled_recovery() { let (_, store) = store(); let sequence = store.queue_sale(&binding(), &command("one"), 1).unwrap(); for now in 2..=4 { store.reconcile(&binding(), &EdgeOperationReconciled { sequence, outcome: ReconciliationOutcome::RetryableFailure, diagnostic: "temporary".into() }, now, 1, 10).unwrap(); } assert!(matches!(store.rollback_migration(), Err(EdgeSyncError::ControlledRecoveryRequired))); }
    #[test]
    fn offline_and_stale_are_visible() { let (_, store) = store(); assert_eq!(store.freshness(false, 10, 5).unwrap().state, FreshnessState::Offline); assert_eq!(store.freshness(true, 10, 5).unwrap().state, FreshnessState::Stale); }
}
