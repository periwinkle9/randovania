from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Container

    from randovania.game_description.resources.resource_collection import ResourceCollection
    from randovania.game_description.resources.resource_info import ResourceInfo
    from randovania.generator.filler.filler_configuration import FillerConfiguration
    from randovania.generator.generator_reach import GeneratorReach
    from randovania.graph.state import State
    from randovania.graph.world_graph import WorldGraph, WorldGraphNode


def _get_reach_class() -> type[GeneratorReach]:
    from randovania.generator.old_generator_reach import OldGeneratorReach

    return OldGeneratorReach


def _action_has_no_dangerous_resources(
    node: WorldGraphNode,
    dangerous_resources: Container[ResourceInfo],
    resources: ResourceCollection,
) -> bool:
    return all(resource not in dangerous_resources for resource, _ in node.resource_gain_on_collect(resources))


def get_collectable_resource_nodes_of_reach(
    reach: GeneratorReach,
    use_safe_nodes: bool = False,
    include_with_dangerous_resources: bool = True,
    must_be_reachable: bool = True,
) -> list[WorldGraphNode]:
    resources = reach.state.resources
    health = reach.state.health_for_damage_requirements
    dangerous_resources = reach.graph.dangerous_resources

    result = []

    node_list = reach.safe_nodes if use_safe_nodes else (reach.connected_nodes if must_be_reachable else reach.nodes)

    for node in node_list:
        if (
            not node.has_all_resources(resources)
            and node.requirement_to_collect.satisfied(resources, health)
            and (
                include_with_dangerous_resources
                or (_action_has_no_dangerous_resources(node, dangerous_resources, resources))
            )
            and (not must_be_reachable or reach.is_reachable_node(node))
        ):
            result.append(node)

    return result


def collect_all_safe_resources_in_reach(reach: GeneratorReach) -> None:
    while True:
        actions = get_collectable_resource_nodes_of_reach(
            reach,
            use_safe_nodes=True,
            include_with_dangerous_resources=False,
        )
        if not actions:
            break

        for action in actions:
            # requirement_to_collect was checked in `_get_safe_resources`, so we're assuming it's already satisfied
            if not action.has_all_resources(reach.state.resources):
                # assert reach.is_safe_node(action)
                reach.advance_to(reach.state.act_on_node(action), is_safe=True)


def reach_with_all_safe_resources(
    graph: WorldGraph, initial_state: State, filler_config: FillerConfiguration
) -> GeneratorReach:
    """
    Creates a new GeneratorReach using the given state and then collect all safe resources
    :param graph:
    :param initial_state:
    :param filler_config:
    :return:
    """
    reach = _get_reach_class().reach_from_state(graph, initial_state, filler_config)
    collect_all_safe_resources_in_reach(reach)
    return reach


def _is_new_node_state_no_worse(
    new_reach,
    old_safe_node_sets: list[set[int]],
    new_reachable_nodes: set[int],
    old_reachable_node_sets: list[set[int]],
) -> bool:
    for old_safe_nodes in old_safe_node_sets:
        if not old_safe_nodes <= new_reach.safe_nodes_index_set:
            return False
    for old_reachable_nodes in old_reachable_node_sets:
        if not old_reachable_nodes <= new_reachable_nodes:
            return False
    return True


def _check_if_action_was_safe(
    graph,
    initial_state,
    previous_reach,
    action,
    new_reach,
    new_reachable_nodes: set[int],
    previous_safe_node_sets: list[set[int]],
    old_reachable_node_sets: list[set[int]],
) -> bool:
    if _is_new_node_state_no_worse(new_reach, previous_safe_node_sets, new_reachable_nodes, old_reachable_node_sets):
        if _action_has_no_dangerous_resources(action, graph.dangerous_resources, initial_state.resources):
            return True

        # It'll only be reachable in the new state if it already is in the existing state
        # And since we already calculated the reachable nodes for this reach, this is a cheap operation!
        if new_reach.is_reachable_node(initial_state.node):
            # In this case, the action provides a dangerous resource but collecting it still lets us go back
            # to where we started and the set of safe nodes didn't shrink
            # We'll now create an entire new GeneratorReach and check if the safe nodes really didn't shrink.

            # No need to call collect_all_safe_resources_in_reach on this new reach,
            # as we've already collected everything at the start of this loop
            experimental_reach = _get_reach_class().reach_from_state(
                graph, new_reach.state.copy(), previous_reach.filler_config
            )

            if previous_safe_node_sets[0] <= experimental_reach.safe_nodes_index_set:
                return True
    return False


def advance_after_action(previous_reach: GeneratorReach) -> GeneratorReach:
    """
    Create a new GeneratorReach that collected actions not considered safe, but expanded the safe_nodes set
    :param previous_reach:
    :return:
    """
    graph = previous_reach.graph
    collect_all_safe_resources_in_reach(previous_reach)
    initial_state = previous_reach.state

    previous_safe_nodes = previous_reach.safe_nodes_index_set
    old_reachable_nodes = previous_reach.set_of_reachable_node_indices()

    for action in get_collectable_resource_nodes_of_reach(previous_reach):
        # print("Trying to collect {} and it's not dangerous. Copying...".format(action.full_name()))
        next_reach = copy.deepcopy(previous_reach)
        next_reach.act_on(action)
        collect_all_safe_resources_in_reach(next_reach)
        middle_safe_nodes = next_reach.safe_nodes_index_set
        middle_reachable_nodes = next_reach.set_of_reachable_node_indices()

        if _check_if_action_was_safe(
            graph,
            initial_state,
            previous_reach,
            action,
            next_reach,
            middle_reachable_nodes,
            previous_safe_node_sets=[previous_safe_nodes],
            old_reachable_node_sets=[old_reachable_nodes],
        ):
            return advance_after_action(next_reach)

        for next_action in get_collectable_resource_nodes_of_reach(next_reach):
            # This loop is largely the same logic as above, and exists for the purpose of trying a second possibly
            # unsafe action to get an even deeper evaluation of new safe resources.

            next_next_reach = copy.deepcopy(next_reach)
            next_next_reach.act_on(next_action)
            collect_all_safe_resources_in_reach(next_next_reach)

            last_reachable_nodes = next_next_reach.set_of_reachable_node_indices()

            if _check_if_action_was_safe(
                graph,
                initial_state,
                previous_reach,
                next_action,
                next_next_reach,
                last_reachable_nodes,
                previous_safe_node_sets=[previous_safe_nodes, middle_safe_nodes],
                old_reachable_node_sets=[old_reachable_nodes, middle_reachable_nodes],
            ):
                return advance_after_action(next_reach)

    # We couldn't improve this reach, so just return it
    return previous_reach
