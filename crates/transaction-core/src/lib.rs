#![forbid(unsafe_code)]

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

/// Returns true when an idempotency key contains at least one visible character.
pub fn is_valid_idempotency_key(value: &str) -> bool {
    !value.trim().is_empty()
}

#[derive(Clone, Debug, Eq, PartialEq, Hash)]
pub struct StockKey {
    pub tenant_id: String,
    pub product_id: String,
    pub location_id: String,
}

impl StockKey {
    pub fn new(tenant_id: impl Into<String>, product_id: impl Into<String>, location_id: impl Into<String>) -> Self {
        Self {
            tenant_id: tenant_id.into(),
            product_id: product_id.into(),
            location_id: location_id.into(),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PartialPolicy {
    RejectPartial,
    AllowPartial,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReservationCommand {
    pub reservation_id: String,
    pub idempotency_key: String,
    pub stock: StockKey,
    pub requested_quantity: i64,
    pub partial_policy: PartialPolicy,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReservationStatus {
    Reserved,
    Released,
    Expired,
    Committed,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Reservation {
    pub reservation_id: String,
    pub stock: StockKey,
    pub quantity: i64,
    pub status: ReservationStatus,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ReservationOutcome {
    Reserved(Reservation),
    Conflict {
        reservation_id: String,
        available_quantity: i64,
    },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReservationChangedEvent {
    pub reservation_id: String,
    pub tenant_id: String,
    pub status: ReservationStatus,
    pub quantity: i64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ReservationError {
    InvalidCommand(&'static str),
    UnknownReservation,
}

#[derive(Default)]
struct ReservationState {
    available: HashMap<StockKey, i64>,
    reservations: HashMap<String, Reservation>,
    outcomes_by_key: HashMap<(String, String), ReservationOutcome>,
    events: Vec<ReservationChangedEvent>,
}

/// Serializes each state transition. The lock is the controlled transaction
/// boundary for this in-process service. Persistence adapters can use the same
/// transition rules inside a database transaction.
#[derive(Clone, Default)]
pub struct ReservationEngine {
    state: Arc<Mutex<ReservationState>>,
}

impl ReservationEngine {
    pub fn with_available(stock: StockKey, quantity: i64) -> Result<Self, ReservationError> {
        if quantity < 0 {
            return Err(ReservationError::InvalidCommand("available quantity cannot be negative"));
        }
        let mut state = ReservationState::default();
        state.available.insert(stock, quantity);
        Ok(Self {
            state: Arc::new(Mutex::new(state)),
        })
    }

    pub fn reserve(&self, command: ReservationCommand) -> Result<ReservationOutcome, ReservationError> {
        Self::validate_command(&command)?;
        let mut state = self.state.lock().expect("reservation state lock poisoned");
        let idempotency_key = (command.stock.tenant_id.clone(), command.idempotency_key.clone());
        if let Some(outcome) = state.outcomes_by_key.get(&idempotency_key) {
            return Ok(outcome.clone());
        }

        let available = *state.available.get(&command.stock).unwrap_or(&0);
        let quantity = match command.partial_policy {
            PartialPolicy::RejectPartial if available < command.requested_quantity => {
                let outcome = ReservationOutcome::Conflict {
                    reservation_id: command.reservation_id,
                    available_quantity: available,
                };
                state.outcomes_by_key.insert(idempotency_key, outcome.clone());
                return Ok(outcome);
            }
            PartialPolicy::AllowPartial => available.min(command.requested_quantity),
            PartialPolicy::RejectPartial => command.requested_quantity,
        };
        if quantity == 0 {
            let outcome = ReservationOutcome::Conflict {
                reservation_id: command.reservation_id,
                available_quantity: available,
            };
            state.outcomes_by_key.insert(idempotency_key, outcome.clone());
            return Ok(outcome);
        }
        if state.reservations.contains_key(&command.reservation_id) {
            return Err(ReservationError::InvalidCommand("reservation id is already in use"));
        }

        state.available.insert(command.stock.clone(), available - quantity);
        let reservation = Reservation {
            reservation_id: command.reservation_id.clone(),
            stock: command.stock,
            quantity,
            status: ReservationStatus::Reserved,
        };
        state.reservations.insert(command.reservation_id, reservation.clone());
        state.events.push(ReservationChangedEvent {
            reservation_id: reservation.reservation_id.clone(),
            tenant_id: reservation.stock.tenant_id.clone(),
            status: ReservationStatus::Reserved,
            quantity,
        });
        let outcome = ReservationOutcome::Reserved(reservation);
        state.outcomes_by_key.insert(idempotency_key, outcome.clone());
        Ok(outcome)
    }

    pub fn release(&self, reservation_id: &str) -> Result<Reservation, ReservationError> {
        self.transition(reservation_id, ReservationStatus::Released)
    }

    pub fn expire(&self, reservation_id: &str) -> Result<Reservation, ReservationError> {
        self.transition(reservation_id, ReservationStatus::Expired)
    }

    pub fn commit(&self, reservation_id: &str) -> Result<Reservation, ReservationError> {
        self.transition(reservation_id, ReservationStatus::Committed)
    }

    pub fn available(&self, stock: &StockKey) -> i64 {
        *self
            .state
            .lock()
            .expect("reservation state lock poisoned")
            .available
            .get(stock)
            .unwrap_or(&0)
    }

    pub fn events(&self) -> Vec<ReservationChangedEvent> {
        self.state
            .lock()
            .expect("reservation state lock poisoned")
            .events
            .clone()
    }

    fn transition(&self, reservation_id: &str, target: ReservationStatus) -> Result<Reservation, ReservationError> {
        let mut state = self.state.lock().expect("reservation state lock poisoned");
        let reservation = state
            .reservations
            .get(reservation_id)
            .cloned()
            .ok_or(ReservationError::UnknownReservation)?;
        if reservation.status != ReservationStatus::Reserved {
            return Ok(reservation);
        }

        let mut changed = reservation;
        changed.status = target;
        if matches!(target, ReservationStatus::Released | ReservationStatus::Expired) {
            *state.available.entry(changed.stock.clone()).or_default() += changed.quantity;
        }
        state.reservations.insert(changed.reservation_id.clone(), changed.clone());
        state.events.push(ReservationChangedEvent {
            reservation_id: changed.reservation_id.clone(),
            tenant_id: changed.stock.tenant_id.clone(),
            status: target,
            quantity: changed.quantity,
        });
        Ok(changed)
    }

    fn validate_command(command: &ReservationCommand) -> Result<(), ReservationError> {
        if command.reservation_id.trim().is_empty() {
            return Err(ReservationError::InvalidCommand("reservation id is required"));
        }
        if !is_valid_idempotency_key(&command.idempotency_key) {
            return Err(ReservationError::InvalidCommand("idempotency key is required"));
        }
        if command.stock.tenant_id.trim().is_empty()
            || command.stock.product_id.trim().is_empty()
            || command.stock.location_id.trim().is_empty()
        {
            return Err(ReservationError::InvalidCommand("stock scope is required"));
        }
        if command.requested_quantity <= 0 {
            return Err(ReservationError::InvalidCommand("requested quantity must be positive"));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread;

    fn stock() -> StockKey {
        StockKey::new("tenant-a", "product-a", "location-a")
    }

    fn command(id: &str, key: &str, quantity: i64) -> ReservationCommand {
        ReservationCommand {
            reservation_id: id.into(),
            idempotency_key: key.into(),
            stock: stock(),
            requested_quantity: quantity,
            partial_policy: PartialPolicy::RejectPartial,
        }
    }

    #[test]
    fn accepts_a_visible_idempotency_key() {
        assert!(is_valid_idempotency_key("sale-123"));
        assert!(!is_valid_idempotency_key(" \t "));
    }

    #[test]
    fn concurrent_requests_do_not_oversell_final_stock() {
        let engine = ReservationEngine::with_available(stock(), 1).unwrap();
        let first = engine.clone();
        let second = engine.clone();
        let a = thread::spawn(move || first.reserve(command("r-1", "key-1", 1)).unwrap());
        let b = thread::spawn(move || second.reserve(command("r-2", "key-2", 1)).unwrap());
        let outcomes = [a.join().unwrap(), b.join().unwrap()];
        assert_eq!(outcomes.iter().filter(|outcome| matches!(outcome, ReservationOutcome::Reserved(_))).count(), 1);
        assert_eq!(engine.available(&stock()), 0);
    }

    #[test]
    fn retry_returns_the_first_committed_result_without_reducing_stock_again() {
        let engine = ReservationEngine::with_available(stock(), 5).unwrap();
        let first = engine.reserve(command("r-1", "sale-1", 3)).unwrap();
        let retry = engine.reserve(command("different-id", "sale-1", 3)).unwrap();
        assert_eq!(first, retry);
        assert_eq!(engine.available(&stock()), 2);
        assert_eq!(engine.events().len(), 1);
    }

    #[test]
    fn release_expiry_and_commit_apply_availability_once() {
        let engine = ReservationEngine::with_available(stock(), 5).unwrap();
        engine.reserve(command("release", "key-release", 2)).unwrap();
        engine.release("release").unwrap();
        engine.release("release").unwrap();
        assert_eq!(engine.available(&stock()), 5);

        engine.reserve(command("expire", "key-expire", 2)).unwrap();
        engine.expire("expire").unwrap();
        engine.expire("expire").unwrap();
        assert_eq!(engine.available(&stock()), 5);

        engine.reserve(command("commit", "key-commit", 2)).unwrap();
        engine.commit("commit").unwrap();
        engine.commit("commit").unwrap();
        assert_eq!(engine.available(&stock()), 3);
        assert_eq!(engine.events().len(), 6);
    }

    #[test]
    fn rejects_unsatisfied_all_or_nothing_reservations_as_a_conflict() {
        let engine = ReservationEngine::with_available(stock(), 1).unwrap();
        assert_eq!(
            engine.reserve(command("r-1", "key-1", 2)).unwrap(),
            ReservationOutcome::Conflict {
                reservation_id: "r-1".into(),
                available_quantity: 1,
            }
        );
    }

    #[test]
    fn allows_a_configured_partial_reservation() {
        let engine = ReservationEngine::with_available(stock(), 1).unwrap();
        let mut partial = command("r-1", "key-1", 2);
        partial.partial_policy = PartialPolicy::AllowPartial;
        assert!(matches!(engine.reserve(partial).unwrap(), ReservationOutcome::Reserved(Reservation { quantity: 1, .. })));
        assert_eq!(engine.available(&stock()), 0);
    }
}
