import os
from datetime import datetime, timedelta

import pytest

from message_cache import MessageCache
from config import Config


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_messages.db"
    monkeypatch.setattr(Config, "DB_PATH", str(db_file))
    # Defer cleanup to cache fixture after closing DB connection (Windows locks files)
    yield str(db_file)


@pytest.fixture()
def cache(temp_db):
    cache = MessageCache(max_size=100)
    try:
        yield cache
    finally:
        # Close SQLite connection to release file handles
        try:
            cache.conn.close()
        except Exception:
            pass
        # Attempt to remove temp DB file
        try:
            if os.path.exists(Config.DB_PATH):
                os.remove(Config.DB_PATH)
        except Exception:
            pass


def _add_messages(cache: MessageCache, chat_id: int, base_time: datetime, count: int = 5, user_id_start: int = 1):
    for i in range(count):
        cache.add_message(
            chat_id=chat_id,
            user_id=user_id_start + (i % 2),
            username=f"user{user_id_start + (i % 2)}",
            text=f"msg {i}",
            timestamp=base_time + timedelta(minutes=i),
        )


def test_add_and_get_last_n_messages(cache):
    base_time = datetime(2024, 1, 1, 12, 0, 0)
    _add_messages(cache, chat_id=111, base_time=base_time, count=7)

    last5 = cache.get_last_n_messages(111, 5)
    assert len(last5) == 5
    # ordered oldest first
    assert last5[0]["text"] == "msg 2"
    assert last5[-1]["text"] == "msg 6"


def test_get_messages_since(cache):
    base_time = datetime(2024, 1, 1, 12, 0, 0)
    _add_messages(cache, chat_id=222, base_time=base_time, count=6)

    since = base_time + timedelta(minutes=3)
    msgs = cache.get_messages_since(222, since)
    # includes messages with timestamp >= since
    assert [m["text"] for m in msgs] == ["msg 3", "msg 4", "msg 5"]


def test_get_chat_stats(cache):
    base_time = datetime(2024, 1, 1, 9, 0, 0)
    _add_messages(cache, chat_id=333, base_time=base_time, count=4)
    stats = cache.get_chat_stats(333)
    assert stats["total_messages"] == 4
    assert stats["unique_users"] == 2
    assert stats["oldest_message"].strftime("%H:%M:%S") == "09:00:00"
    assert stats["newest_message"].strftime("%H:%M:%S") == "09:03:00"


def test_get_user_messages_and_limit(cache):
    base_time = datetime(2024, 1, 1, 10, 0, 0)
    # add 6 messages alternating users 5 and 6
    _add_messages(cache, chat_id=444, base_time=base_time, count=6, user_id_start=5)

    user5_all = cache.get_user_messages(444, 5)
    assert len(user5_all) == 3
    assert [m["user_id"] for m in user5_all] == [5, 5, 5]

    user6_last2 = cache.get_user_messages(444, 6, limit=2)
    assert len(user6_last2) == 2
    assert [m["text"] for m in user6_last2] == ["msg 3", "msg 5"]


def test_get_user_interactions(cache):
    base_time = datetime(2024, 1, 1, 8, 0, 0)
    chat_id = 555
    # Build a small conversation window around user 42
    cache.add_message(chat_id, 1, "alice", "hello", base_time)
    cache.add_message(chat_id, 42, "target", "hi", base_time + timedelta(minutes=1))
    cache.add_message(chat_id, 2, "bob", "how are you?", base_time + timedelta(minutes=2))
    cache.add_message(chat_id, 42, "target", "fine", base_time + timedelta(minutes=3))
    cache.add_message(chat_id, 3, "carol", "ok", base_time + timedelta(minutes=4))

    interactions = cache.get_user_interactions(chat_id, 42)
    assert "self" in interactions
    # should include partners 'alice', 'bob', 'carol'
    partners = set(k for k in interactions.keys() if k != "self")
    assert {"alice", "bob", "carol"}.issubset(partners)

