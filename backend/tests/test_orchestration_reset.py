from types import SimpleNamespace

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from edrak.orchestration.state import RESET, merge_by_task_id


def test_reset_sentinel_is_checkpoint_serializable():
    # A full replan writes RESET into channel writes; the SQLite checkpointer
    # must be able to msgpack it.
    kind, _ = JsonPlusSerializer().dumps_typed(RESET)
    assert kind == "msgpack"


def test_reset_clears_the_channel_and_normal_merge_replaces_by_task_id():
    left = [SimpleNamespace(task_id="a"), SimpleNamespace(task_id="b")]
    assert merge_by_task_id(left, RESET) == []
    merged = merge_by_task_id(left, [SimpleNamespace(task_id="b"), SimpleNamespace(task_id="c")])
    assert sorted(item.task_id for item in merged) == ["a", "b", "c"]
