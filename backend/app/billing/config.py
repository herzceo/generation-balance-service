from backend.internal.dto import StructDTO


class BillingConfig(StructDTO):
    RUNNING_WAIT_TIMEOUT_SECONDS: float = 30.0
    RUNNING_POLL_INTERVAL_SECONDS: float = 0.02
    RESERVE_MAX_ATTEMPTS: int = 100
    # Must exceed RedisBalanceStoreConfig.LOAD_LOCK_TTL_MS so a crashed lock holder
    # still leaves losers time for a second load attempt.
    LOAD_WAIT_TIMEOUT_SECONDS: float = 15.0
    FLUSH_INTERVAL_SECONDS: float = 1.0
    FLUSH_BATCH_SIZE: int = 500
    REAP_AFTER_SECONDS: float = 300.0
    REAP_BATCH_SIZE: int = 100
