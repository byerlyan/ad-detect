from ad_detect.capture import BoundedFanout, DropOldestChannel


def test_drop_oldest_channel_reports_overflow() -> None:
    channel = DropOldestChannel[int](capacity=2)
    channel.publish_nowait(1)
    channel.publish_nowait(2)
    channel.publish_nowait(3)

    assert channel.receive_nowait() == 2
    assert channel.receive_nowait() == 3
    assert channel.stats.published == 3
    assert channel.stats.delivered == 2
    assert channel.stats.dropped == 1


def test_fanout_isolates_slow_consumers() -> None:
    fanout = BoundedFanout[int]()
    fast = fanout.subscribe("fast", capacity=3)
    slow = fanout.subscribe("slow", capacity=1)
    for value in range(3):
        fanout.publish_nowait(value)

    assert [fast.receive_nowait() for _ in range(3)] == [0, 1, 2]
    assert slow.receive_nowait() == 2
    assert fanout.stats()["slow"].dropped == 2
