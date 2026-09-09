from foundation.fulfillment_store import PostgresFulfillmentStore


class Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.rows: list[tuple[object, ...]] = []

    def execute(self, query: str, parameters: tuple[object, ...] = ()) -> None:
        self.executed.append((query, parameters))

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class Connection:
    def __init__(self) -> None:
        self.cursor_value = Cursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> Cursor:
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_transaction_commits_fulfillment_transition_and_outbox_together() -> None:
    connection = Connection()
    store = PostgresFulfillmentStore(connection)
    with store.transaction() as cursor:
        store.write_transition_and_outbox(
            cursor,
            "fulfillment-a",
            "tenant-a",
            None,
            "promised",
            "actor-a",
            "trace-a",
            "t-a",
            "e-a",
        )
    assert connection.commits == 1
    assert len(connection.cursor_value.executed) == 2


def test_transaction_rolls_back_when_the_domain_operation_fails() -> None:
    connection = Connection()
    store = PostgresFulfillmentStore(connection)
    try:
        with store.transaction():
            raise RuntimeError("database failure")
    except RuntimeError:
        pass
    assert connection.rollbacks == 1


def test_restart_recovery_reads_active_tenant_fulfillments() -> None:
    connection = Connection()
    connection.cursor_value.rows = [
        (
            "fulfillment-a",
            "tenant-a",
            "store-a",
            "warehouse-a",
            "order-a",
            "customer-a",
            "payment-a",
            "reservation-a",
            "delivery",
            "slot-a",
            "1 Main Street",
            "dispatched",
            "driver-a",
            None,
            None,
            None,
        )
    ]
    store = PostgresFulfillmentStore(connection)
    with store.transaction() as cursor:
        active = store.active_for_tenant(cursor, "tenant-a")
    assert active[0].status == "dispatched"
    assert active[0].tenant_id == "tenant-a"
