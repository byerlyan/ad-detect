"""Bounded in-process channels that prefer fresh live data over backlog."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ChannelStats:
    published: int
    delivered: int
    dropped: int
    depth: int
    capacity: int


class DropOldestChannel(Generic[T]):
    """A bounded channel whose overflow policy is explicit and observable."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=capacity)
        self._published = 0
        self._delivered = 0
        self._dropped = 0

    def publish_nowait(self, item: T) -> None:
        self._published += 1
        if self._queue.full():
            self._queue.get_nowait()
            self._dropped += 1
        self._queue.put_nowait(item)

    async def receive(self) -> T:
        item = await self._queue.get()
        self._delivered += 1
        return item

    def receive_nowait(self) -> T:
        item = self._queue.get_nowait()
        self._delivered += 1
        return item

    @property
    def stats(self) -> ChannelStats:
        return ChannelStats(
            published=self._published,
            delivered=self._delivered,
            dropped=self._dropped,
            depth=self._queue.qsize(),
            capacity=self._queue.maxsize,
        )


class BoundedFanout(Generic[T]):
    """Publish immutable records to isolated per-consumer bounded channels."""

    def __init__(self) -> None:
        self._subscribers: dict[str, DropOldestChannel[T]] = {}

    def subscribe(self, name: str, capacity: int) -> DropOldestChannel[T]:
        if name in self._subscribers:
            raise ValueError(f"subscriber already exists: {name}")
        channel = DropOldestChannel[T](capacity)
        self._subscribers[name] = channel
        return channel

    def unsubscribe(self, name: str) -> None:
        self._subscribers.pop(name, None)

    def publish_nowait(self, item: T) -> None:
        for channel in tuple(self._subscribers.values()):
            channel.publish_nowait(item)

    def stats(self) -> dict[str, ChannelStats]:
        return {name: channel.stats for name, channel in self._subscribers.items()}
