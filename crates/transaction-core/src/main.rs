use std::collections::HashMap;
use std::io::{self, BufRead, Write};

use serde::{Deserialize, Serialize};
use transaction_core::{
    PartialPolicy, Reservation, ReservationCommand, ReservationEngine, ReservationError,
    ReservationOutcome, ReservationStatus, StockKey,
};

#[derive(Deserialize)]
#[serde(tag = "version", rename_all = "snake_case")]
enum Request {
    #[serde(rename = "v1")]
    V1 { command: Command },
}

#[derive(Deserialize, Clone)]
#[serde(tag = "type", rename_all = "snake_case")]
enum Command {
    Initialize { availability: Vec<StockInput> },
    Reserve {
        reservation_id: String,
        idempotency_key: String,
        tenant_id: String,
        store_id: String,
        warehouse_id: String,
        product_id: String,
        quantity: i64,
        partial_policy: String,
    },
    Confirm { reservation_id: String },
    Expire { reservation_id: String },
    Release { reservation_id: String },
    Status { reservation_id: String },
    Availability { tenant_id: String, warehouse_id: String, product_id: String },
}

#[derive(Deserialize, Clone)]
struct StockInput { tenant_id: String, warehouse_id: String, product_id: String, quantity: i64 }

#[derive(Serialize)]
struct Response {
    version: &'static str,
    #[serde(flatten)]
    result: ResultBody,
}

#[derive(Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum ResultBody {
    Reservation { reservation: ReservationBody },
    Conflict { reservation_id: String, available_quantity: i64 },
    Availability { quantity: i64 },
    Error { code: &'static str, message: String, retryable: bool },
}

#[derive(Serialize, Clone)]
struct ReservationBody {
    reservation_id: String,
    tenant_id: String,
    product_id: String,
    warehouse_id: String,
    quantity: i64,
    status: &'static str,
}

fn status_name(status: ReservationStatus) -> &'static str {
    match status { ReservationStatus::Reserved => "reserved", ReservationStatus::Released => "released", ReservationStatus::Expired => "expired", ReservationStatus::Committed => "committed" }
}

fn body(reservation: Reservation) -> ReservationBody {
    ReservationBody { reservation_id: reservation.reservation_id, tenant_id: reservation.stock.tenant_id, product_id: reservation.stock.product_id, warehouse_id: reservation.stock.location_id, quantity: reservation.quantity, status: status_name(reservation.status) }
}

fn reply(result: ResultBody) -> Response { Response { version: "v1", result } }

fn reservation_result(result: Result<Reservation, ReservationError>, cache: &mut HashMap<String, ReservationBody>) -> Response {
    match result {
        Ok(reservation) => { let reservation = body(reservation); cache.insert(reservation.reservation_id.clone(), reservation.clone()); reply(ResultBody::Reservation { reservation }) }
        Err(error) => reply(ResultBody::Error { code: "reservation_error", message: format!("{error:?}"), retryable: false }),
    }
}

fn main() {
    let stdin = io::stdin();
    let mut output = io::stdout();
    let mut engine: Option<ReservationEngine> = None;
    let mut cache: HashMap<String, ReservationBody> = HashMap::new();
    for line in stdin.lock().lines() {
        let response = match line {
            Err(error) => reply(ResultBody::Error { code: "transport_error", message: error.to_string(), retryable: true }),
            Ok(line) => match serde_json::from_str::<Request>(&line) {
                Err(error) => reply(ResultBody::Error { code: "invalid_request", message: error.to_string(), retryable: false }),
                Ok(Request::V1 { command }) => match command {
                    Command::Initialize { availability } => {
                        let mut next = ReservationEngine::default();
                        let mut error = None;
                        for stock in availability { match ReservationEngine::with_available(StockKey::new(stock.tenant_id, stock.product_id, stock.warehouse_id), stock.quantity) { Ok(value) => next = value, Err(value) => { error = Some(value); break; } } }
                        if let Some(error) = error { reply(ResultBody::Error { code: "invalid_request", message: format!("{error:?}"), retryable: false }) } else { engine = Some(next); reply(ResultBody::Availability { quantity: 0 }) }
                    }
                    command => match engine.as_ref() {
                        None => reply(ResultBody::Error { code: "not_initialized", message: "initialize before using reservation runtime".into(), retryable: false }),
                        Some(engine) => match command {
                            Command::Reserve { reservation_id, idempotency_key, tenant_id, store_id: _, warehouse_id, product_id, quantity, partial_policy } => {
                                let partial_policy = if partial_policy == "allow_partial" { PartialPolicy::AllowPartial } else { PartialPolicy::RejectPartial };
                                match engine.reserve(ReservationCommand { reservation_id, idempotency_key, stock: StockKey::new(tenant_id, product_id, warehouse_id), requested_quantity: quantity, partial_policy }) {
                                    Ok(ReservationOutcome::Reserved(value)) => { let value = body(value); cache.insert(value.reservation_id.clone(), value.clone()); reply(ResultBody::Reservation { reservation: value }) }
                                    Ok(ReservationOutcome::Conflict { reservation_id, available_quantity }) => reply(ResultBody::Conflict { reservation_id, available_quantity }),
                                    Err(error) => reply(ResultBody::Error { code: "reservation_error", message: format!("{error:?}"), retryable: false }),
                                }
                            }
                            Command::Confirm { reservation_id } => reservation_result(engine.commit(&reservation_id), &mut cache),
                            Command::Expire { reservation_id } => reservation_result(engine.expire(&reservation_id), &mut cache),
                            Command::Release { reservation_id } => reservation_result(engine.release(&reservation_id), &mut cache),
                            Command::Status { reservation_id } => match cache.get(&reservation_id) { Some(value) => reply(ResultBody::Reservation { reservation: value.clone() }), None => reply(ResultBody::Error { code: "unknown_reservation", message: "reservation is unknown".into(), retryable: false }) },
                            Command::Availability { tenant_id, warehouse_id, product_id } => reply(ResultBody::Availability { quantity: engine.available(&StockKey::new(tenant_id, product_id, warehouse_id)) }),
                            Command::Initialize { .. } => unreachable!(),
                        },
                    },
                },
            },
        };
        writeln!(output, "{}", serde_json::to_string(&response).expect("response serializes")).expect("write response");
        output.flush().expect("flush response");
    }
}
