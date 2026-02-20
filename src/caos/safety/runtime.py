"""Runtime Safety Guards — operational protections (doc §6, §7).

Implements:
- SO_001 Anti-Flapping: blocks decision flips within 30s per asset
- SO_002 Prompt-Injection: pattern-based detection on trigger payloads
- SO_003/SO_004 Fail-Safe: service health monitoring, degraded-mode fallback
- SO_005 Rate Limiter: request/min and daily count tracking
- SO_007 Exec Timeout: pipeline duration enforcement
- SO_008 Behavioral Anomaly: action frequency vs baseline detection
- ROB_001 Mutex: prevents concurrent workflows on the same asset
- ROB_002 Circuit Breaker: LLM daily token/cost budget enforcement
- ROB_004 Event Dedup: duplicate event_id detection and skip
- ROB_005 Backpressure: in-flight event queue depth management
- COMM_002 Anti-Spam: aggregates repeated alerts (>5/min same alert)
- COMM Notification Router: severity-based channel routing
"""

import re
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# =============================================
# SO_001  Anti-Flapping
# =============================================

@dataclass
class _DecisionRecord:
    decision_band: str
    action_type: str
    timestamp: float


class AntiFlapping:
    """Blocks contradictory decisions for the same asset within a cooldown window.
    
    If the system oscillates (e.g. EXECUTE→BLOCKED→EXECUTE) for the same asset
    within `cooldown_seconds`, the later decision is suppressed and an ALERT
    is raised instead (doc §7.4.1).
    """

    def __init__(self, cooldown_seconds: float = 30.0, max_history: int = 10):
        self._cooldown = cooldown_seconds
        self._max_history = max_history
        self._history: dict[str, list[_DecisionRecord]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, asset_id: str, decision_band: str, action_type: str) -> dict | None:
        """Check for flapping. Returns violation dict if flapping detected, else None."""
        now = time.monotonic()
        with self._lock:
            records = self._history[asset_id]
            # Purge old entries
            records[:] = [r for r in records if now - r.timestamp < self._cooldown]

            # Check for flip: last decision != current
            if records and records[-1].decision_band != decision_band:
                elapsed = now - records[-1].timestamp
                if elapsed < self._cooldown:
                    violation = {
                        "id": "SO_001",
                        "description": (
                            f"Anti-flapping: decisão mudou de {records[-1].decision_band} "
                            f"para {decision_band} em {elapsed:.1f}s (cooldown={self._cooldown}s)"
                        ),
                        "severity": "HIGH",
                        "previous_decision": records[-1].decision_band,
                        "current_decision": decision_band,
                    }
                    logger.warning("anti_flapping_triggered", asset_id=asset_id, **violation)
                    return violation

            # Record
            records.append(_DecisionRecord(decision_band, action_type, now))
            if len(records) > self._max_history:
                records[:] = records[-self._max_history:]

        return None


# =============================================
# SO_002  Prompt-Injection Detection
# =============================================

# Patterns commonly used to hijack LLM context
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(previous|all|above)\s+(instructions?|rules?|constraints?)", re.I),
    re.compile(r"you\s+are\s+now\s+(a|an|the)\s+", re.I),
    re.compile(r"(system|admin)\s*:\s*", re.I),
    re.compile(r"forget\s+(everything|all|your)\s+", re.I),
    re.compile(r"\bdisregard\b.*\binstructions?\b", re.I),
    re.compile(r"<\s*/?s(cript|tyle)\b", re.I),
    re.compile(r"\{\{.*\}\}", re.I),          # template injection
    re.compile(r"\\x[0-9a-f]{2}", re.I),      # hex escape injection
    re.compile(r"\\u[0-9a-f]{4}", re.I),       # unicode escape injection
    re.compile(r"do\s+not\s+follow\s+", re.I),
    re.compile(r"jailbreak|DAN\s+mode|dev\s+mode", re.I),
]


def detect_prompt_injection(text: str) -> dict | None:
    """Scan a text for prompt-injection patterns. Returns violation dict or None."""
    if not text:
        return None
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            violation = {
                "id": "SO_002",
                "description": f"Prompt injection detectada: '{match.group()}'",
                "severity": "BLOCKING",
                "matched_pattern": match.group(),
            }
            logger.warning("prompt_injection_detected", text_snippet=text[:200], **violation)
            return violation
    return None


def check_trigger_injection(trigger_payload: dict) -> dict | None:
    """Check all string fields of a trigger payload for prompt injection."""
    fields_to_check = []
    for key in ("metric", "description", "message", "raw_payload"):
        v = trigger_payload.get(key)
        if isinstance(v, str):
            fields_to_check.append(v)
    payload = trigger_payload.get("payload")
    if isinstance(payload, dict):
        for v in payload.values():
            if isinstance(v, str):
                fields_to_check.append(v)
    for text in fields_to_check:
        result = detect_prompt_injection(text)
        if result:
            return result
    return None


# =============================================
# ROB_001  Mutex (Global por Asset)
# =============================================

class AssetMutex:
    """Prevents concurrent CAOS workflows for the same asset_id.
    
    If a workflow is already in progress for an asset, new triggers
    are queued or rejected (doc §7.6.1).
    """

    def __init__(self):
        self._active: dict[str, float] = {}  # asset_id → start timestamp
        self._lock = threading.Lock()
        self._ttl = 120.0  # max lock duration seconds

    def try_acquire(self, asset_id: str) -> dict | None:
        """Try to acquire the mutex. Returns violation dict if already held."""
        now = time.monotonic()
        with self._lock:
            # Clean stale locks
            stale = [k for k, t in self._active.items() if now - t > self._ttl]
            for k in stale:
                del self._active[k]
                logger.info("mutex_stale_released", asset_id=k)

            if asset_id in self._active:
                held_for = now - self._active[asset_id]
                violation = {
                    "id": "ROB_001",
                    "description": (
                        f"Mutex: workflow já em andamento para {asset_id} "
                        f"há {held_for:.1f}s"
                    ),
                    "severity": "HIGH",
                    "asset_id": asset_id,
                }
                logger.warning("mutex_blocked", **violation)
                return violation

            self._active[asset_id] = now
            logger.debug("mutex_acquired", asset_id=asset_id)
            return None

    def release(self, asset_id: str) -> None:
        """Release the mutex for an asset."""
        with self._lock:
            self._active.pop(asset_id, None)
            logger.debug("mutex_released", asset_id=asset_id)


# =============================================
# ROB_002  Circuit Breaker (LLM Budget)
# =============================================

class CircuitBreakerBackend:
    """Abstract storage backend for Circuit Breaker state."""
    def record(self, tokens: int, cost: float) -> None:
        raise NotImplementedError
    
    def get_usage(self) -> tuple[int, float]:
        raise NotImplementedError

class MemoryBackend(CircuitBreakerBackend):
    """Local in-memory backend (dev/test only)."""
    def __init__(self):
        self._tokens = 0
        self._cost = 0.0
        self._day = ""
        self._lock = threading.Lock()
    
    def _reset_if_new_day(self):
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._tokens = 0
            self._cost = 0.0
            self._day = today

    def record(self, tokens: int, cost: float) -> None:
        with self._lock:
            self._reset_if_new_day()
            self._tokens += tokens
            self._cost += cost

    def get_usage(self) -> tuple[int, float]:
        with self._lock:
            self._reset_if_new_day()
            return self._tokens, self._cost

class RedisBackend(CircuitBreakerBackend):
    """Shared Redis backend (production)."""
    def __init__(self, redis_url: str):
        import redis
        self.client = redis.from_url(redis_url)
        self._ttl = 86400 # 24h

    def _get_keys(self) -> tuple[str, str]:
        today = time.strftime("%Y-%m-%d")
        return f"cb:tokens:{today}", f"cb:cost:{today}"

    def record(self, tokens: int, cost: float) -> None:
        k_tok, k_cost = self._get_keys()
        pipe = self.client.pipeline()
        pipe.incrby(k_tok, tokens)
        pipe.expire(k_tok, self._ttl)
        if cost > 0:
            pipe.incrbyfloat(k_cost, cost)
            pipe.expire(k_cost, self._ttl)
        pipe.execute()

    def get_usage(self) -> tuple[int, float]:
        k_tok, k_cost = self._get_keys()
        t, c = self.client.mget(k_tok, k_cost)
        return int(t or 0), float(c or 0.0)


class CircuitBreaker:
    """Enforces daily LLM token and cost budgets.
    
    When budget is exceeded, CAOS falls back to code-only mode (doc §7.6.2).
    Resets daily.
    """

    def __init__(self, backend: CircuitBreakerBackend, max_tokens: int = 2_000_000, max_cost_usd: float = 50.0):
        self.backend = backend
        self.max_tokens = max_tokens
        self.max_cost_usd = max_cost_usd

    def record_usage(self, tokens: int, cost_usd: float = 0.0) -> None:
        """Record LLM usage."""
        self.backend.record(tokens, cost_usd)

    def is_tripped(self) -> bool:
        """Check if circuit breaker is tripped (budget exceeded)."""
        t, c = self.backend.get_usage()
        return t >= self.max_tokens or c >= self.max_cost_usd

    def get_status(self) -> dict:
        """Get current budget status."""
        t, c = self.backend.get_usage()
        tripped = t >= self.max_tokens or c >= self.max_cost_usd
        return {
            "tokens_used": t,
            "tokens_limit": self.max_tokens,
            "cost_used_usd": c,
            "cost_limit_usd": self.max_cost_usd,
            "tripped": tripped,
        }

# ... (omitted AntiSpam code) ...

def get_circuit_breaker() -> CircuitBreaker:
    from caos._registry import get, put
    cb = get("circuit_breaker")
    if cb is None:
        from caos.config import get_settings
        s = get_settings()
        
        if s.redis_url:
            backend = RedisBackend(s.redis_url)
            logger.info("circuit_breaker_backend", type="redis")
        else:
            backend = MemoryBackend()
            logger.info("circuit_breaker_backend", type="memory")
            
        cb = CircuitBreaker(
            backend=backend,
            max_tokens=s.llm_daily_token_budget,
            max_cost_usd=s.llm_daily_cost_budget_usd
        )
        put("circuit_breaker", cb)
    return cb

# =============================================
# COMM_002  Anti-Spam (Alert Aggregation)
# =============================================

@dataclass
class _AlertBucket:
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    suppressed: int = 0


class AntiSpam:
    """Aggregates repeated identical alerts into digest batches.
    
    If the same alert signature fires >threshold times within window,
    subsequent alerts are suppressed and a single digest is produced (doc §7.8.2).
    """

    def __init__(self, threshold: int = 5, window_seconds: float = 60.0):
        self._threshold = threshold
        self._window = window_seconds
        self._buckets: dict[str, _AlertBucket] = {}
        self._lock = threading.Lock()

    def _signature(self, asset_id: str, metric: str | None, severity: str) -> str:
        return f"{asset_id}|{metric or 'none'}|{severity}"

    def check(self, asset_id: str, metric: str | None, severity: str) -> dict | None:
        """Check for spam. Returns violation dict if threshold exceeded, else None."""
        now = time.monotonic()
        sig = self._signature(asset_id, metric, severity)

        with self._lock:
            bucket = self._buckets.get(sig)
            if bucket is None or (now - bucket.first_seen) > self._window:
                # New window
                self._buckets[sig] = _AlertBucket(count=1, first_seen=now, last_seen=now)
                return None

            bucket.count += 1
            bucket.last_seen = now

            if bucket.count > self._threshold:
                bucket.suppressed += 1
                violation = {
                    "id": "COMM_002",
                    "description": (
                        f"Anti-spam: {bucket.count} alertas idênticos para {asset_id} "
                        f"({metric}/{severity}) em {now - bucket.first_seen:.0f}s "
                        f"(threshold={self._threshold}/{self._window:.0f}s)"
                    ),
                    "severity": "HIGH",
                    "suppressed_count": bucket.suppressed,
                    "total_count": bucket.count,
                }
                logger.warning("anti_spam_triggered", asset_id=asset_id, **violation)
                return violation

        return None

    def get_digest(self, asset_id: str | None = None) -> list[dict]:
        """Get suppressed alert digests, optionally filtered by asset."""
        now = time.monotonic()
        digests = []
        with self._lock:
            for sig, bucket in self._buckets.items():
                if bucket.suppressed > 0 and (now - bucket.first_seen) < self._window * 2:
                    parts = sig.split("|")
                    a_id = parts[0] if parts else "?"
                    if asset_id and a_id != asset_id:
                        continue
                    digests.append({
                        "asset_id": a_id,
                        "metric": parts[1] if len(parts) > 1 else None,
                        "severity": parts[2] if len(parts) > 2 else None,
                        "total_alerts": bucket.count,
                        "suppressed": bucket.suppressed,
                        "window_start": bucket.first_seen,
                    })
        return digests


# =============================================
# SO_005  Rate Limiter
# =============================================

class RateLimiter:
    """Tracks request rates per minute and daily totals.

    Used both for SO_005 (rate limiting) and to populate ``daily_actions``
    and ``api_calls_per_minute`` in guardrail evaluation context.
    """

    def __init__(self, max_per_minute: int = 1000):
        self._max_per_minute = max_per_minute
        self._minute_buckets: list[float] = []
        self._daily_count: int = 0
        self._day: str = ""
        self._lock = threading.Lock()

    def _reset_if_new_day(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._daily_count = 0
            self._day = today

    def record(self) -> dict | None:
        """Record a request. Returns violation dict if rate limit exceeded."""
        now = time.monotonic()
        with self._lock:
            self._reset_if_new_day()
            self._daily_count += 1
            # Purge entries older than 60s
            self._minute_buckets = [t for t in self._minute_buckets if now - t < 60.0]
            self._minute_buckets.append(now)

            if len(self._minute_buckets) > self._max_per_minute:
                violation = {
                    "id": "SO_005",
                    "description": (
                        f"Rate limit excedido: {len(self._minute_buckets)} "
                        f"requests/min (max={self._max_per_minute})"
                    ),
                    "severity": "HIGH",
                }
                logger.warning("rate_limit_exceeded", **violation)
                return violation
        return None

    def get_requests_per_minute(self) -> int:
        now = time.monotonic()
        with self._lock:
            self._minute_buckets = [t for t in self._minute_buckets if now - t < 60.0]
            return len(self._minute_buckets)

    def get_daily_count(self) -> int:
        with self._lock:
            self._reset_if_new_day()
            return self._daily_count


# =============================================
# ROB_004  Event Deduplication
# =============================================

class EventDedup:
    """Detects and skips duplicate event IDs within a TTL window.

    Uses an in-memory cache. In production this would back to Redis/Firestore.
    """

    def __init__(self, ttl_seconds: float = 300.0, max_size: int = 10_000):
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._cache: dict[str, float] = {}  # event_id → timestamp
        self._lock = threading.Lock()

    def check(self, event_id: str) -> dict | None:
        """Returns violation dict if event_id is a duplicate, else None (and records it)."""
        now = time.monotonic()
        with self._lock:
            # Purge expired entries periodically
            if len(self._cache) > self._max_size:
                cutoff = now - self._ttl
                self._cache = {k: v for k, v in self._cache.items() if v > cutoff}

            if event_id in self._cache:
                elapsed = now - self._cache[event_id]
                if elapsed < self._ttl:
                    violation = {
                        "id": "ROB_004",
                        "description": (
                            f"Evento duplicado: {event_id} processado {elapsed:.1f}s atrás"
                        ),
                        "severity": "LOW",
                    }
                    logger.info("event_dedup_skipped", event_id=event_id, elapsed=elapsed)
                    return violation

            self._cache[event_id] = now
        return None


# =============================================
# SO_008  Behavioral Anomaly Detection
# =============================================

class BehavioralAnomaly:
    """Detects unusual action frequency compared to a rolling baseline.

    If action count for an asset in the current window exceeds 3x the
    average of previous windows, emits an ALERT.
    """

    def __init__(self, window_seconds: float = 300.0, history_windows: int = 12):
        self._window = window_seconds
        self._history_windows = history_windows
        self._current: dict[str, int] = defaultdict(int)  # asset_id → count this window
        self._history: dict[str, list[int]] = defaultdict(list)  # asset_id → [past window counts]
        self._window_start: float = time.monotonic()
        self._lock = threading.Lock()

    def _maybe_rotate(self) -> None:
        now = time.monotonic()
        if now - self._window_start >= self._window:
            # Rotate current into history
            for asset_id, count in self._current.items():
                h = self._history[asset_id]
                h.append(count)
                if len(h) > self._history_windows:
                    self._history[asset_id] = h[-self._history_windows:]
            self._current = defaultdict(int)
            self._window_start = now

    def record(self, asset_id: str) -> dict | None:
        """Record an action. Returns ALERT violation if anomalous."""
        with self._lock:
            self._maybe_rotate()
            self._current[asset_id] += 1

            h = self._history.get(asset_id, [])
            if len(h) >= 3:
                baseline = sum(h) / len(h)
                if baseline > 0 and self._current[asset_id] > baseline * 3:
                    violation = {
                        "id": "SO_008",
                        "description": (
                            f"Anomalia comportamental: {self._current[asset_id]} ações "
                            f"para {asset_id} nesta janela (baseline={baseline:.1f})"
                        ),
                        "severity": "MEDIUM",
                    }
                    logger.warning("behavioral_anomaly", asset_id=asset_id, **violation)
                    return violation
        return None


# =============================================
# SO_003/SO_004  Fail-Safe Protocol
# =============================================

class FailSafe:
    """Monitors external service health and triggers degraded mode.

    When critical services (Atlas, Oracle, CARE/Pub/Sub) are unreachable,
    the system enters "Local Fallback" mode:
    - CRITICAL alerts → hardcoded shutdown
    - HIGH alerts → forward to Co-Pilot without LLM analysis
    - MEDIUM/LOW alerts → log and ignore until recovery

    Doc §7.4: SO3/SO4 — Fail-Safe Protocol.
    """

    def __init__(self, check_interval: float = 30.0) -> None:
        self._check_interval = check_interval
        self._service_status: dict[str, bool] = {
            "atlas": True,
            "oracle": True,
            "pubsub": True,
        }
        self._last_check: dict[str, float] = {}
        self._lock = threading.Lock()

    def report_service_up(self, service: str) -> None:
        """Mark a service as healthy after a successful call."""
        with self._lock:
            if service in self._service_status:
                was_down = not self._service_status[service]
                self._service_status[service] = True
                self._last_check[service] = time.monotonic()
                if was_down:
                    logger.info("failsafe_service_recovered", service=service)

    def report_service_down(self, service: str, error: str = "") -> None:
        """Mark a service as unhealthy after a failed call."""
        with self._lock:
            if service in self._service_status:
                was_up = self._service_status[service]
                self._service_status[service] = False
                self._last_check[service] = time.monotonic()
                if was_up:
                    logger.critical(
                        "failsafe_service_down",
                        service=service,
                        error=error,
                    )

    def is_degraded(self) -> bool:
        """Check if any critical service is down → degraded mode."""
        with self._lock:
            return not all(self._service_status.values())

    def get_status(self) -> dict[str, Any]:
        """Get full service health status."""
        with self._lock:
            return {
                "degraded": not all(self._service_status.values()),
                "services": dict(self._service_status),
                "last_check": dict(self._last_check),
            }

    def get_degraded_action(self, severity: str) -> dict | None:
        """If in degraded mode, return the appropriate fallback action.

        Returns None if NOT degraded (normal operation).
        Returns a violation dict with fallback instructions if degraded.
        """
        if not self.is_degraded():
            return None

        status = self.get_status()
        down_services = [s for s, up in status["services"].items() if not up]

        if severity == "CRITICAL":
            return {
                "id": "SO_003",
                "description": (
                    f"FAIL-SAFE ATIVO: serviços {down_services} indisponíveis. "
                    "Executando shutdown hardcoded para alerta CRITICAL."
                ),
                "severity": "BLOCKING",
                "fallback_action": "hardcoded_shutdown",
            }
        elif severity == "HIGH":
            return {
                "id": "SO_003",
                "description": (
                    f"FAIL-SAFE ATIVO: serviços {down_services} indisponíveis. "
                    "Encaminhando para Co-Pilot sem análise LLM."
                ),
                "severity": "HIGH",
                "fallback_action": "forward_to_copilot",
            }
        else:
            # MEDIUM / LOW → log and ignore until recovery
            return {
                "id": "SO_004",
                "description": (
                    f"FAIL-SAFE ATIVO: serviços {down_services} indisponíveis. "
                    f"Alerta {severity} logado e ignorado até recovery."
                ),
                "severity": "MEDIUM",
                "fallback_action": "log_and_ignore",
            }


# =============================================
# ROB_005  Backpressure Management
# =============================================

class BackpressureGuard:
    """Tracks in-flight events and applies backpressure when queue depth is high.

    If more than ``max_inflight`` events are being processed concurrently,
    new events are rejected with a DEGRADE response until the queue drains.
    """

    def __init__(self, max_inflight: int = 50) -> None:
        self._max_inflight = max_inflight
        self._inflight: int = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> dict | None:
        """Try to enter the processing queue.

        Returns None if accepted, violation dict if backpressure activated.
        The caller MUST call ``release()`` when done.
        """
        with self._lock:
            if self._inflight >= self._max_inflight:
                violation = {
                    "id": "ROB_005",
                    "description": (
                        f"Backpressure: {self._inflight} eventos em processamento "
                        f"(max={self._max_inflight}). Evento rejeitado."
                    ),
                    "severity": "MEDIUM",
                }
                logger.warning("backpressure_triggered", inflight=self._inflight)
                return violation
            self._inflight += 1
            return None

    def release(self) -> None:
        """Release a slot after event processing completes."""
        with self._lock:
            self._inflight = max(0, self._inflight - 1)

    def get_depth(self) -> int:
        """Current queue depth."""
        with self._lock:
            return self._inflight


# =============================================
# COMM  Notification Channel Router
# =============================================

class NotificationRouter:
    """Routes notifications to appropriate channels based on severity.

    Channel matrix (doc §8.3):
      CRITICAL → WhatsApp (30min) + SMS (15min) + Dashboard (4h)
      HIGH     → WhatsApp (30min) + Dashboard (4h)
      MEDIUM   → Email (24h) + Dashboard (4h)
      LOW      → Dashboard (4h, auto-approve after timeout)
    """

    # Channel definitions: (channel_name, timeout_minutes, auto_approve)
    CHANNEL_MATRIX: dict[str, list[tuple[str, int, bool]]] = {
        "CRITICAL": [
            ("whatsapp", 30, False),
            ("sms", 15, False),
            ("dashboard", 240, False),
        ],
        "HIGH": [
            ("whatsapp", 30, False),
            ("dashboard", 240, False),
        ],
        "MEDIUM": [
            ("email", 1440, True),
            ("dashboard", 240, True),
        ],
        "LOW": [
            ("dashboard", 240, True),
        ],
    }

    def __init__(self) -> None:
        self._sent_log: list[dict] = []
        self._lock = threading.Lock()

    def route(
        self,
        severity: str,
        asset_id: str,
        message: str,
        tenant_id: str = "",
        action_id: str = "",
    ) -> list[dict]:
        """Determine and log notification channels for a given severity.

        Returns a list of channel dispatch records.
        In production these would call Twilio / SendGrid / etc.
        """
        channels = self.CHANNEL_MATRIX.get(severity, self.CHANNEL_MATRIX["LOW"])
        dispatches: list[dict] = []
        now = time.time()

        for channel_name, timeout_min, auto_approve in channels:
            record = {
                "channel": channel_name,
                "severity": severity,
                "asset_id": asset_id,
                "tenant_id": tenant_id,
                "action_id": action_id,
                "message": message[:500],
                "timeout_minutes": timeout_min,
                "auto_approve_on_timeout": auto_approve,
                "dispatched_at": now,
            }
            dispatches.append(record)
            logger.info(
                "notification_dispatched",
                channel=channel_name,
                severity=severity,
                asset_id=asset_id,
                timeout_min=timeout_min,
            )

        with self._lock:
            self._sent_log.extend(dispatches)
            # Keep only last 1000 entries
            if len(self._sent_log) > 1000:
                self._sent_log = self._sent_log[-1000:]

        return dispatches

    def get_channel_for_severity(self, severity: str) -> list[str]:
        """Return channel names for a given severity level."""
        channels = self.CHANNEL_MATRIX.get(severity, self.CHANNEL_MATRIX["LOW"])
        return [c[0] for c in channels]

    def get_recent_dispatches(self, limit: int = 50) -> list[dict]:
        """Return recent notification dispatches for observability."""
        with self._lock:
            return list(reversed(self._sent_log[-limit:]))


# =============================================
# Singletons via registry
# =============================================

def get_anti_flapping() -> AntiFlapping:
    from caos._registry import get, put
    af = get("anti_flapping")
    if af is None:
        af = AntiFlapping()
        put("anti_flapping", af)
    return af


def get_anti_spam() -> AntiSpam:
    from caos._registry import get, put
    ans = get("anti_spam")
    if ans is None:
        ans = AntiSpam()
        put("anti_spam", ans)
    return ans


def get_asset_mutex() -> AssetMutex:
    from caos._registry import get, put
    m = get("asset_mutex")
    if m is None:
        m = AssetMutex()
        put("asset_mutex", m)
    return m





def get_rate_limiter() -> RateLimiter:
    from caos._registry import get, put
    rl = get("rate_limiter")
    if rl is None:
        rl = RateLimiter()
        put("rate_limiter", rl)
    return rl


def get_event_dedup() -> EventDedup:
    from caos._registry import get, put
    ed = get("event_dedup")
    if ed is None:
        ed = EventDedup()
        put("event_dedup", ed)
    return ed


def get_behavioral_anomaly() -> BehavioralAnomaly:
    from caos._registry import get, put
    ba = get("behavioral_anomaly")
    if ba is None:
        ba = BehavioralAnomaly()
        put("behavioral_anomaly", ba)
    return ba


def get_fail_safe() -> FailSafe:
    from caos._registry import get, put
    fs = get("fail_safe")
    if fs is None:
        fs = FailSafe()
        put("fail_safe", fs)
    return fs


def get_backpressure_guard() -> BackpressureGuard:
    from caos._registry import get, put
    bp = get("backpressure_guard")
    if bp is None:
        bp = BackpressureGuard()
        put("backpressure_guard", bp)
    return bp


def get_notification_router() -> NotificationRouter:
    from caos._registry import get, put
    nr = get("notification_router")
    if nr is None:
        nr = NotificationRouter()
        put("notification_router", nr)
    return nr
