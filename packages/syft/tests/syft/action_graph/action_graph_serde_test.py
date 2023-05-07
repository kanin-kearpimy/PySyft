# stdlib
from typing import Any

# third party
import pytest
from pytest import FixtureRequest

# syft absolute
import syft as sy
from syft.node.credentials import SyftVerifyKey
from syft.service.action.action_graph import InMemoryActionGraphStore
from syft.service.action.action_graph import NodeActionData
from syft.service.action.action_object import Action
from syft.service.action.action_object import ActionObject


def test_node_action_data_serde(verify_key: SyftVerifyKey) -> None:
    action_obj = ActionObject.from_obj([1, 2, 3])
    action = Action(
        path="action.execute",
        op="np.array",
        remote_self=None,
        args=[action_obj.syft_lineage_id],
        kwargs={},
    )
    node_action_data = NodeActionData.from_action(action=action, credentials=verify_key)
    bytes_data: bytes = sy.serialize(node_action_data, to_bytes=True)
    deserialized_node_action_data = sy.deserialize(bytes_data, from_bytes=True)

    assert deserialized_node_action_data == node_action_data


@pytest.mark.parametrize(
    "obj",
    [
        "simple_in_memory_action_graph",
        "complicated_in_memory_action_graph",
        "mutated_in_memory_action_graph",
    ],
)
def test_in_memory_action_graph_serde(obj: Any, request: FixtureRequest) -> None:
    in_memory_graph: InMemoryActionGraphStore = request.getfixturevalue(obj)
    serialized_graph: bytes = sy.serialize(in_memory_graph, to_bytes=True)
    deserialized_graph = sy.deserialize(serialized_graph, from_bytes=True)

    assert isinstance(deserialized_graph, type(in_memory_graph))
    assert isinstance(deserialized_graph.graph, type(in_memory_graph.graph))
    assert isinstance(deserialized_graph.graph.db, type(in_memory_graph.graph.db))
    assert deserialized_graph.edges == in_memory_graph.edges
    assert deserialized_graph.nodes == in_memory_graph.nodes
