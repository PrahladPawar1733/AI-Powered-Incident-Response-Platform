# shared/kafka_utils.py
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable, Awaitable
from typing import Any

from confluent_kafka import Consumer, Producer, KafkaError, KafkaException, Message

from shared.logger import get_logger

log = get_logger("kafka-utils")


class KafkaProducer:
    """
    Thin async-friendly wrapper around confluent-kafka Producer.

    Why confluent-kafka over kafka-python?
    confluent-kafka is built on librdkafka (C library) — 3-5x faster,
    better error handling, actively maintained by Confluent.
    """

    def __init__(self, bootstrap_servers: str):
        self._producer = Producer({
            "bootstrap.servers": bootstrap_servers,
            "acks": "all",              # wait for all replicas to confirm
            "retries": 3,
            "retry.backoff.ms": 300,
            "compression.type": "snappy",
            "linger.ms": 5,             # batch messages for 5ms before sending
        })

    def publish(
        self,
        topic: str,
        value: dict[str, Any],
        key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """
        Publish a message to a Kafka topic.

        key: used for partition assignment. Messages with the same key
             always go to the same partition — guaranteeing ordering.
             For incidents, use incident_id as key so all messages
             for one incident stay ordered.

        headers: used for trace_id propagation (Phase 8).
                 OpenTelemetry trace context travels in Kafka headers.
        """
        kafka_headers = []
        if headers:
            kafka_headers = [(k, v.encode()) for k, v in headers.items()]

        self._producer.produce(
            topic=topic,
            value=json.dumps(value, default=str).encode(),
            key=key.encode() if key else None,
            headers=kafka_headers,
            on_delivery=self._on_delivery,
        )
        # flush() ensures the message is actually sent, not just buffered
        self._producer.poll(0)

    def flush(self, timeout: float = 10.0) -> None:
        self._producer.flush(timeout)

    def _on_delivery(self, err: KafkaError | None, msg: Message) -> None:
        if err:
            log.error("kafka_delivery_failed",
                      topic=msg.topic(), error=str(err))
        else:
            log.debug("kafka_delivered",
                      topic=msg.topic(), partition=msg.partition(),
                      offset=msg.offset())


class KafkaConsumer:
    """
    Base class for all Kafka consumers in this platform.

    Each agent service subclasses this and implements handle_message().
    The consume_loop() handles:
    - polling for messages
    - deserializing JSON
    - extracting trace headers
    - calling your handler
    - committing offset ONLY after successful processing
    - dead letter queue on repeated failures (Phase 6)
    """

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str,
        topics: list[str],
        auto_offset_reset: str = "earliest",
    ):
        self._consumer = Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": auto_offset_reset,
            "enable.auto.commit": False,    # we commit manually after processing
            "max.poll.interval.ms": 300_000,  # 5 min — allows slow agent calls
            "session.timeout.ms": 30_000,
        })
        self._consumer.subscribe(topics)
        self._running = False
        self.log = get_logger(group_id)

    async def consume_loop(
        self,
        handler: Callable[[dict[str, Any], dict[str, str]], Awaitable[None]],
    ) -> None:
        """
        handler receives:
          - message: the deserialized JSON payload
          - headers: dict of Kafka message headers (trace_id lives here)
        """
        self._running = True
        self.log.info("consumer_started", topics=self._consumer.assignment())

        while self._running:
            msg = self._consumer.poll(timeout=1.0)

            if msg is None:
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue   # end of partition, not an error
                self.log.error("kafka_consumer_error", error=str(msg.error()))
                continue

            try:
                payload = json.loads(msg.value().decode("utf-8"))
                headers = self._extract_headers(msg)

                self.log.info(
                    "message_received",
                    topic=msg.topic(),
                    partition=msg.partition(),
                    offset=msg.offset(),
                    trace_id=headers.get("trace_id"),
                )

                await handler(payload, headers)

                # Commit AFTER successful processing
                self._consumer.commit(msg)

            except Exception as exc:
                self.log.error(
                    "message_processing_failed",
                    error=str(exc),
                    topic=msg.topic(),
                    offset=msg.offset(),
                    exc_info=True,
                )
                # Don't commit — message will be redelivered
                # Phase 6 adds retry topic + DLQ logic here

    def stop(self) -> None:
        self._running = False
        self._consumer.close()

    def _extract_headers(self, msg: Message) -> dict[str, str]:
        """Pull Kafka headers into a plain dict."""
        headers = {}
        if msg.headers():
            for key, value in msg.headers():
                if value is not None:
                    headers[key] = value.decode("utf-8")
        return headers