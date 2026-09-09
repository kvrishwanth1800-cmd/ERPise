use postgres::{Client, NoTls, Row, Transaction};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::env;
use std::io::{self, BufRead, Write};

const SCHEMA_VERSION: &str = "v1";

#[derive(Deserialize)]
#[serde(tag = "version", rename_all = "snake_case")]
enum Request {
    #[serde(rename = "v1")]
    V1 { command: Command },
}

#[derive(Deserialize)]
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
        expires_at: String,
        trace_id: String,
    },
    Confirm { tenant_id: String, reservation_id: String, trace_id: String },
    Expire { tenant_id: String, reservation_id: String, trace_id: String },
    Release { tenant_id: String, reservation_id: String, trace_id: String },
    ExpireDue { trace_id: String },
    Status { tenant_id: String, reservation_id: String },
    Availability { tenant_id: String, store_id: String, warehouse_id: String, product_id: String },
}

#[derive(Deserialize)]
struct StockInput {
    tenant_id: String,
    store_id: String,
    warehouse_id: String,
    product_id: String,
    quantity: i64,
}

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
    Expired { reservation_ids: Vec<String> },
    Error { code: &'static str, message: String, retryable: bool },
}

#[derive(Serialize)]
struct ReservationBody {
    reservation_id: String,
    tenant_id: String,
    store_id: String,
    warehouse_id: String,
    product_id: String,
    quantity: i64,
    status: String,
    expires_at: String,
}

fn response(result: ResultBody) -> Response {
    Response { version: SCHEMA_VERSION, result }
}

fn error(code: &'static str, message: impl Into<String>, retryable: bool) -> Response {
    response(ResultBody::Error { code, message: message.into(), retryable })
}

fn reservation(row: &Row) -> ReservationBody {
    ReservationBody {
        reservation_id: row.get("reservation_id"),
        tenant_id: row.get("tenant_id"),
        store_id: row.get("store_id"),
        warehouse_id: row.get("warehouse_id"),
        product_id: row.get("product_id"),
        quantity: row.get("quantity"),
        status: row.get("status"),
        expires_at: row.get::<_, String>("expires_at"),
    }
}

fn insert_event(tx: &mut Transaction<'_>, row: &ReservationBody, trace_id: &str) -> Result<(), postgres::Error> {
    let event_id = format!("ReservationChanged-{}-{}", row.reservation_id, row.status);
    tx.execute(
        "INSERT INTO durable_outbox_records (event_id, tenant_id, event_type, schema_version, trace_id, payload, occurred_at) \
         VALUES ($1, $2, 'ReservationChanged', 'v1', $3, $4::jsonb, now()) ON CONFLICT (event_id) DO NOTHING",
        &[&event_id, &row.tenant_id, &trace_id, &json!({
            "reservation_id": row.reservation_id,
            "store_id": row.store_id,
            "warehouse_id": row.warehouse_id,
            "product_id": row.product_id,
            "quantity": row.quantity,
            "status": row.status,
            "expires_at": row.expires_at,
        }).to_string()],
    )?;
    Ok(())
}

fn transition(client: &mut Client, tenant_id: &str, reservation_id: &str, target: &str, trace_id: &str) -> Response {
    let mut tx = match client.transaction() { Ok(value) => value, Err(value) => return error("database_error", value, true) };
    let row = match tx.query_opt(
        "SELECT reservation_id, tenant_id, store_id, warehouse_id, product_id, quantity, status, expires_at::text \
         FROM durable_reservations WHERE tenant_id = $1 AND reservation_id = $2 FOR UPDATE",
        &[&tenant_id, &reservation_id],
    ) { Ok(Some(value)) => value, Ok(None) => return error("unknown_reservation", "reservation is outside tenant scope or unknown", false), Err(value) => return error("database_error", value, true) };
    let current = reservation(&row);
    if current.status != "reserved" {
        return response(ResultBody::Reservation { reservation: current });
    }
    if target == "expired" && tx.query_one("SELECT now() >= $1::timestamptz", &[&current.expires_at]).map_or(false, |row| row.get(0)) == false {
        return response(ResultBody::Reservation { reservation: current });
    }
    if target == "released" || target == "expired" {
        if let Err(value) = tx.execute(
            "UPDATE reservation_stock SET available_quantity = available_quantity + $1, version = version + 1, updated_at = now() \
             WHERE tenant_id = $2 AND store_id = $3 AND warehouse_id = $4 AND product_id = $5",
            &[&current.quantity, &current.tenant_id, &current.store_id, &current.warehouse_id, &current.product_id],
        ) { return error("database_error", value, true); }
    }
    let changed = match tx.query_one(
        "UPDATE durable_reservations SET status = $1, version = version + 1, updated_at = now() \
         WHERE reservation_id = $2 RETURNING reservation_id, tenant_id, store_id, warehouse_id, product_id, quantity, status, expires_at::text",
        &[&target, &reservation_id],
    ) { Ok(value) => reservation(&value), Err(value) => return error("database_error", value, true) };
    if let Err(value) = insert_event(&mut tx, &changed, trace_id).and_then(|_| tx.commit()) { return error("database_error", value, true); }
    response(ResultBody::Reservation { reservation: changed })
}

fn reserve(client: &mut Client, command: Command) -> Response {
    let Command::Reserve { reservation_id, idempotency_key, tenant_id, store_id, warehouse_id, product_id, quantity, expires_at, trace_id } = command else { unreachable!() };
    if [reservation_id.as_str(), idempotency_key.as_str(), tenant_id.as_str(), store_id.as_str(), warehouse_id.as_str(), product_id.as_str(), expires_at.as_str(), trace_id.as_str()].iter().any(|value| value.trim().is_empty()) || quantity <= 0 {
        return error("invalid_request", "reservation scope, expiry, trace and positive quantity are required", false);
    }
    let mut tx = match client.transaction() { Ok(value) => value, Err(value) => return error("database_error", value, true) };
    match tx.query_opt("SELECT reservation_id, tenant_id, store_id, warehouse_id, product_id, quantity, status, expires_at::text FROM durable_reservations WHERE tenant_id = $1 AND idempotency_key = $2", &[&tenant_id, &idempotency_key]) {
        Ok(Some(row)) => return response(ResultBody::Reservation { reservation: reservation(&row) }),
        Ok(None) => {},
        Err(value) => return error("database_error", value, true),
    }
    let stock = match tx.query_opt(
        "SELECT available_quantity FROM reservation_stock WHERE tenant_id = $1 AND store_id = $2 AND warehouse_id = $3 AND product_id = $4 FOR UPDATE",
        &[&tenant_id, &store_id, &warehouse_id, &product_id],
    ) { Ok(Some(row)) => row.get::<_, i64>(0), Ok(None) => 0, Err(value) => return error("database_error", value, true) };
    if stock < quantity { return response(ResultBody::Conflict { reservation_id, available_quantity: stock }); }
    if let Err(value) = tx.execute(
        "UPDATE reservation_stock SET available_quantity = available_quantity - $1, version = version + 1, updated_at = now() \
         WHERE tenant_id = $2 AND store_id = $3 AND warehouse_id = $4 AND product_id = $5",
        &[&quantity, &tenant_id, &store_id, &warehouse_id, &product_id],
    ) { return error("database_error", value, true); }
    let row = match tx.query_one(
        "INSERT INTO durable_reservations (reservation_id, tenant_id, store_id, warehouse_id, product_id, quantity, idempotency_key, status, expires_at, trace_id) \
         VALUES ($1, $2, $3, $4, $5, $6, $7, 'reserved', $8::timestamptz, $9) \
         RETURNING reservation_id, tenant_id, store_id, warehouse_id, product_id, quantity, status, expires_at::text",
        &[&reservation_id, &tenant_id, &store_id, &warehouse_id, &product_id, &quantity, &idempotency_key, &expires_at, &trace_id],
    ) { Ok(value) => reservation(&value), Err(value) => return error("database_error", value, true) };
    if let Err(value) = insert_event(&mut tx, &row, &trace_id).and_then(|_| tx.commit()) { return error("database_error", value, true); }
    response(ResultBody::Reservation { reservation: row })
}

fn handle(client: &mut Client, command: Command) -> Response {
    match command {
        Command::Initialize { availability } => {
            let mut tx = match client.transaction() { Ok(value) => value, Err(value) => return error("database_error", value, true) };
            for stock in availability {
                if stock.quantity < 0 { return error("invalid_request", "availability cannot be negative", false); }
                if let Err(value) = tx.execute(
                    "INSERT INTO reservation_stock (tenant_id, store_id, warehouse_id, product_id, available_quantity) VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING",
                    &[&stock.tenant_id, &stock.store_id, &stock.warehouse_id, &stock.product_id, &stock.quantity],
                ) { return error("database_error", value, true); }
            }
            match tx.commit() { Ok(()) => response(ResultBody::Availability { quantity: 0 }), Err(value) => error("database_error", value, true) }
        }
        command @ Command::Reserve { .. } => reserve(client, command),
        Command::Confirm { tenant_id, reservation_id, trace_id } => transition(client, &tenant_id, &reservation_id, "committed", &trace_id),
        Command::Release { tenant_id, reservation_id, trace_id } => transition(client, &tenant_id, &reservation_id, "released", &trace_id),
        Command::Expire { tenant_id, reservation_id, trace_id } => transition(client, &tenant_id, &reservation_id, "expired", &trace_id),
        Command::ExpireDue { trace_id } => {
            let rows = match client.query("SELECT tenant_id, reservation_id FROM durable_reservations WHERE status = 'reserved' AND expires_at <= now() ORDER BY expires_at FOR UPDATE SKIP LOCKED", &[]) { Ok(value) => value, Err(value) => return error("database_error", value, true) };
            let ids = rows.iter().filter_map(|row| match transition(client, row.get::<_, String>(0).as_str(), row.get::<_, String>(1).as_str(), "expired", &trace_id).result { ResultBody::Reservation { reservation } => Some(reservation.reservation_id), _ => None }).collect();
            response(ResultBody::Expired { reservation_ids: ids })
        }
        Command::Status { tenant_id, reservation_id } => match client.query_opt("SELECT reservation_id, tenant_id, store_id, warehouse_id, product_id, quantity, status, expires_at::text FROM durable_reservations WHERE tenant_id = $1 AND reservation_id = $2", &[&tenant_id, &reservation_id]) { Ok(Some(row)) => response(ResultBody::Reservation { reservation: reservation(&row) }), Ok(None) => error("unknown_reservation", "reservation is outside tenant scope or unknown", false), Err(value) => error("database_error", value, true) },
        Command::Availability { tenant_id, store_id, warehouse_id, product_id } => match client.query_opt("SELECT available_quantity FROM reservation_stock WHERE tenant_id = $1 AND store_id = $2 AND warehouse_id = $3 AND product_id = $4", &[&tenant_id, &store_id, &warehouse_id, &product_id]) { Ok(row) => response(ResultBody::Availability { quantity: row.map_or(0, |value| value.get(0)) }), Err(value) => error("database_error", value, true) },
    }
}

fn main() {
    let database_url = match env::var("RESERVATION_DATABASE_URL") { Ok(value) => value, Err(_) => { eprintln!("RESERVATION_DATABASE_URL is required"); return; } };
    let mut client = match Client::connect(&database_url, NoTls) { Ok(value) => value, Err(value) => { eprintln!("cannot connect reservation database: {value}"); return; } };
    for line in io::stdin().lock().lines() {
        let output = match line.and_then(|value| Ok(serde_json::from_str::<Request>(&value))) { Ok(Ok(Request::V1 { command })) => handle(&mut client, command), Ok(Err(value)) => error("invalid_request", value.to_string(), false), Err(value) => error("transport_error", value.to_string(), true) };
        println!("{}", serde_json::to_string(&output).expect("response serializes"));
        let _ = io::stdout().flush();
    }
}
