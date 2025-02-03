from __future__ import annotations

import dataclasses
import math
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable, Collection, Mapping, Sequence
from enum import Enum
from random import Random
from typing import TYPE_CHECKING, Any, override

from randovania.game_description.db.hint_node import HintNode, HintNodeKind
from randovania.game_description.db.pickup_node import PickupNode
from randovania.game_description.game_patches import GamePatches
from randovania.game_description.hint import (
    PRECISION_PAIR_UNASSIGNED,
    HintItemPrecision,
    HintLocationPrecision,
    JokeHint,
    LocationHint,
    PrecisionPair,
    is_unassigned_location,
)
from randovania.game_description.hint_features import HintFeature, PickupHintFeature
from randovania.game_description.resources.pickup_index import PickupIndex
from randovania.generator.filler.filler_library import UnableToGenerate
from randovania.generator.filler.player_state import HintState, PlayerState
from randovania.resolver import debug

if TYPE_CHECKING:
    from randovania.game_description.db.node_identifier import NodeIdentifier
    from randovania.game_description.db.region_list import RegionList
    from randovania.game_description.pickup.pickup_entry import PickupEntry
    from randovania.generator.filler.filler_configuration import PlayerPool
    from randovania.generator.pre_fill_params import PreFillParams

HintProvider = Callable[[PlayerState, GamePatches, Random, PickupIndex], LocationHint | None]

HintTargetPrecision = tuple[PickupIndex, PrecisionPair]

HintFeatureGaussianParams = tuple[float, float]
"""The mean and standard deviation defining a Gaussian distribution."""


class FeatureChooser[FeatureT: HintFeature, PrecisionT: Enum]:
    def __init__(
        self,
        total_elements: int,
        elements_with_feature: Mapping[FeatureT | PrecisionT, Collection[Any]],
        detailed_precision: PrecisionT,
    ):
        self.total_elements = total_elements
        self.elements_with_feature = elements_with_feature
        self.detailed_precision = detailed_precision

    def feature_precisions(self) -> dict[FeatureT | PrecisionT, float]:
        """
        Determine the precision of all provided Features,
        as a percentage from `0.0` to `1.0`.
        """

        # arbitrarily increased until it felt good
        DEGREE = 3
        feature_precisions = {
            feature: math.pow(
                ((self.total_elements - len(self.elements_with_feature[feature])) / (self.total_elements - 1)), DEGREE
            )
            for feature in self.elements_with_feature
        }
        feature_precisions = {
            feature: ft_precision
            for feature, ft_precision in feature_precisions.items()
            if ft_precision < 1.0
            # exclude any features that would only point to a single element
        }
        feature_precisions[self.detailed_precision] = 1.0

        return feature_precisions

    def debug_precisions(self, header: str) -> None:
        """Debug print `feature_precisions()`"""

        debug.debug_print(f"> {header}:")
        for feature, precision in self.feature_precisions().items():
            name = feature.name if isinstance(feature, Enum) else feature.long_name
            debug.debug_print(f" * {name}: {precision}")
        debug.debug_print("")

    def choose_feature(
        self,
        element_features: Collection[FeatureT],
        additional_precision_features: Collection[PrecisionT],
        rng: Random,
        mean: float,
        std_dev: float,
    ) -> FeatureT | PrecisionT:
        """
        Randomly choose a hint feature from `element_features`, weighted based on their precision.


        Precision is calculated based on how many possible elements have that feature,
        proportional to the total number of elements.

        The feature is chosen by selecting the feature with the closest precision
        to a gaussian random variable parametrized by `mean` and `std_dev`.
        """
        feature_precisions = self.feature_precisions()

        target_precision = rng.gauss(mean, std_dev)
        target_precision = min(max(target_precision, 0.0), 1.0)
        debug.debug_print(f"  * Target precision: {target_precision}")

        possible_features: list[FeatureT | PrecisionT] = []
        possible_features.extend(sorted(feature for feature in element_features if feature in feature_precisions))
        possible_features.extend(additional_precision_features)
        possible_precisions = {feature: feature_precisions[feature] for feature in possible_features}
        debug.debug_print(f"  * Possible precisions: {possible_precisions}")

        # find feature closest to the chosen precision
        feature = min(possible_features, key=lambda f: abs(feature_precisions[f] - target_precision))
        debug.debug_print(f"  * Closest precision: {feature} ({feature_precisions[feature] - target_precision})\n")

        return feature


class HintDistributor(ABC):
    @property
    def num_joke_hints(self) -> int:
        return 0

    def get_generic_hint_nodes(self, prefill: PreFillParams) -> list[NodeIdentifier]:
        return [
            node.identifier
            for node in prefill.game.region_list.iterate_nodes()
            if isinstance(node, HintNode) and node.kind == HintNodeKind.GENERIC
        ]

    async def get_specific_pickup_precision_pairs(self) -> dict[NodeIdentifier, PrecisionPair]:
        """Assigns a PrecisionPair to each HintNode with kind SPECIFIC_PICKUP in the game's database."""
        return {}

    async def assign_specific_location_hints(self, patches: GamePatches, prefill: PreFillParams) -> GamePatches:
        specific_location_precisions = await self.get_specific_pickup_precision_pairs()

        wl = prefill.game.region_list
        for node in wl.iterate_nodes():
            if isinstance(node, HintNode) and node.kind == HintNodeKind.SPECIFIC_PICKUP:
                identifier = node.identifier
                patches = patches.assign_hint(
                    identifier,
                    LocationHint(
                        specific_location_precisions[identifier],
                        PickupIndex(node.extra["hint_index"]),
                    ),
                )

        return patches

    async def get_guaranteed_hints(self, patches: GamePatches, prefill: PreFillParams) -> list[HintTargetPrecision]:
        return []

    async def assign_guaranteed_indices_hints(
        self, patches: GamePatches, identifiers: list[NodeIdentifier], prefill: PreFillParams
    ) -> GamePatches:
        # Specific Pickup/any HintNode
        indices_with_hint = await self.get_guaranteed_hints(patches, prefill)
        prefill.rng.shuffle(indices_with_hint)

        all_hint_identifiers = [identifier for identifier in identifiers if identifier not in patches.hints]
        prefill.rng.shuffle(all_hint_identifiers)

        for index, precision in indices_with_hint:
            if not all_hint_identifiers:
                break

            identifier = all_hint_identifiers.pop()
            patches = patches.assign_hint(identifier, LocationHint(precision, index))
            identifiers.remove(identifier)

        return patches

    async def assign_other_hints(
        self, patches: GamePatches, identifiers: list[NodeIdentifier], prefill: PreFillParams
    ) -> GamePatches:
        return patches

    async def assign_joke_hints(
        self, patches: GamePatches, identifiers: list[NodeIdentifier], prefill: PreFillParams
    ) -> GamePatches:
        all_hint_identifiers = [identifier for identifier in identifiers if identifier not in patches.hints]
        prefill.rng.shuffle(all_hint_identifiers)

        num_joke = self.num_joke_hints

        while num_joke > 0 and all_hint_identifiers:
            identifier = all_hint_identifiers.pop()
            patches = patches.assign_hint(identifier, JokeHint())
            num_joke -= 1
            identifiers.remove(identifier)

        return patches

    async def assign_pre_filler_hints(
        self, patches: GamePatches, prefill: PreFillParams, rng_required: bool = True
    ) -> GamePatches:
        patches = await self.assign_specific_location_hints(patches, prefill)
        hint_identifiers = self.get_generic_hint_nodes(prefill)
        if rng_required or prefill.rng is not None:
            prefill.rng.shuffle(hint_identifiers)
            patches = await self.assign_guaranteed_indices_hints(patches, hint_identifiers, prefill)
            patches = await self.assign_other_hints(patches, hint_identifiers, prefill)
            patches = await self.assign_joke_hints(patches, hint_identifiers, prefill)
        return patches

    async def assign_post_filler_hints(
        self,
        patches: GamePatches,
        rng: Random,
        player_pool: PlayerPool,
        player_state: PlayerState,
        player_pools: list[PlayerPool],
    ) -> GamePatches:
        # Since we haven't added expansions yet, these hints will always be for items added by the filler.
        full_hints_patches = self.fill_unassigned_hints(
            patches,
            player_state.game.region_list,
            rng,
            player_state.hint_state,
        )
        return await self.assign_precision_to_hints(full_hints_patches, rng, player_pool, player_state, player_pools)

    async def assign_precision_to_hints(
        self,
        patches: GamePatches,
        rng: Random,
        player_pool: PlayerPool,
        player_state: PlayerState,
        player_pools: list[PlayerPool],
    ) -> GamePatches:
        """
        Ensures no hints present in `patches` has no precision.
        :param patches:
        :param rng:
        :param player_pool:
        :param player_state:
        :return:
        """
        return self.add_hints_precision(player_state, patches, rng, player_pools)

    def interesting_pickup_to_hint(self, pickup: PickupEntry) -> bool:
        """Highest priority pickups are those shown in the credits"""
        return pickup.show_in_credits_spoiler and self.less_interesting_pickup_to_hint(pickup)

    def less_interesting_pickup_to_hint(self, pickup: PickupEntry) -> bool:
        """Certain games may want certain pickups to only be hinted as a last resort"""
        return True

    def fill_unassigned_hints(
        self,
        patches: GamePatches,
        region_list: RegionList,
        rng: Random,
        hint_state: HintState,
    ) -> GamePatches:
        """
        Selects targets for all remaining unassigned generic hint nodes
        """

        hinted_pickups = {hint.target for hint in patches.hints.values() if isinstance(hint, LocationHint)}

        def sort_hints(item: tuple[NodeIdentifier, set[PickupIndex]]) -> tuple[int, NodeIdentifier]:
            return (len(item[1] - hinted_pickups), item[0])

        # assign hints with fewest potential targets first to help ensure none of them run out of options
        for hint_node, potential_targets in sorted(hint_state.hint_valid_targets.items(), key=sort_hints):
            if hint_node in patches.hints:
                # hint has already been assigned
                continue

            node = region_list.typed_node_by_identifier(hint_node, HintNode)
            if node.kind != HintNodeKind.GENERIC:
                continue

            debug.debug_print(f"> Choosing hint target for {hint_node.as_string}:")

            # exclude uninteresting pickups (minors, Echoes keys, etc.)
            real_potential_targets = {
                target
                for target in potential_targets
                if self.interesting_pickup_to_hint(patches.pickup_assignment[target].pickup)
            }
            # don't hint things twice
            real_potential_targets -= hinted_pickups

            if not real_potential_targets:
                # no interesting pickups to place - use anything placed by the generator
                real_potential_targets = {
                    pickup
                    for pickup, entry in patches.pickup_assignment.items()
                    if self.less_interesting_pickup_to_hint(entry.pickup)
                }
                real_potential_targets -= hinted_pickups
                debug.debug_print(
                    f"  * No interesting pickups; trying {len(real_potential_targets)} less interesting pickups"
                )

            if not real_potential_targets:
                # STILL no pickups to place - just use *anything* that hasn't been hinted
                real_potential_targets = {
                    node.pickup_index for node in region_list.iterate_nodes() if isinstance(node, PickupNode)
                }
                real_potential_targets -= hinted_pickups
                debug.debug_print(
                    f"  * Still no viable pickups; trying {len(real_potential_targets)} uninteresting pickups"
                )

            if not real_potential_targets:
                raise UnableToGenerate("Not enough PickupNodes in the game to fill all hint locations.")

            target = rng.choice(sorted(real_potential_targets))

            hinted_pickups.add(target)
            debug.debug_print(
                f"  * Placing hint for {patches.pickup_assignment.get(target, target)}"
                f" at {region_list.node_name(region_list.node_from_pickup_index(target))}\n"
            )

            patches = patches.assign_hint(hint_node, LocationHint.unassigned(target))

        return patches

    def replace_hints_without_precision_with_jokes(self, patches: GamePatches) -> GamePatches:
        """
        Assigns a JokeHint to each hint node with unassigned precision
        :param patches:
        :return:
        """

        hints_to_replace = {
            hint_node: JokeHint() for hint_node, hint in patches.hints.items() if is_unassigned_location(hint)
        }

        return dataclasses.replace(
            patches,
            hints={hint_node: hints_to_replace.get(hint_node, hint) for hint_node, hint in patches.hints.items()},
        )

    @property
    @abstractmethod
    def default_precision_pair(self) -> PrecisionPair:
        """The default PrecisionPair to use for unassigned generic hints."""
        raise NotImplementedError

    def get_location_feature_chooser(
        self, patches: GamePatches, location: PickupNode | None = None
    ) -> FeatureChooser[HintFeature, HintLocationPrecision]:
        """Create a FeatureChooser for location Features"""

        region_list = patches.game.region_list
        locations_with_feature: dict[HintFeature | HintLocationPrecision, list[PickupNode]] = defaultdict(list)
        relevant_locations: list[PickupNode] = []

        relevant_locations.extend(node for node in region_list.iterate_nodes() if isinstance(node, PickupNode))
        for feature in patches.game.hint_feature_database.values():
            locations_with_feature[feature].extend(region_list.pickup_nodes_with_feature(feature))

        if location is not None:
            locations_with_feature[HintLocationPrecision.REGION_ONLY] = [
                node for node in region_list.nodes_to_region(location).all_nodes if isinstance(node, PickupNode)
            ]

        return FeatureChooser[HintFeature, HintLocationPrecision](
            len(relevant_locations),
            locations_with_feature,
            HintLocationPrecision.DETAILED,
        )

    def get_pickup_feature_chooser(
        self,
        player_pools: Sequence[PlayerPool],
    ) -> FeatureChooser[PickupHintFeature, HintItemPrecision]:
        """Create a FeatureChooser for pickup Features"""

        pickups_with_feature: dict[PickupHintFeature | HintItemPrecision, list[PickupEntry]] = defaultdict(list)
        relevant_pickups: list[PickupEntry] = []

        for pool in player_pools:
            for pickup in pool.pickups:
                relevant_pickups.append(pickup)
                for feature in pickup.hint_features:
                    pickups_with_feature[feature].append(pickup)

        return FeatureChooser[PickupHintFeature, HintItemPrecision](
            len(relevant_pickups),
            pickups_with_feature,
            HintItemPrecision.DETAILED,
        )

    def get_hint_precision(
        self,
        hint_node: NodeIdentifier,
        hint: LocationHint,
        rng: Random,
        patches: GamePatches,
        player_pools: Sequence[PlayerPool],
    ) -> PrecisionPair:
        """
        Determines the final PrecisionPair for a given hint, filling in any unassigned or featural precisions.
        """

        region_list = patches.game.region_list

        precision = hint.precision
        if precision is PRECISION_PAIR_UNASSIGNED:
            precision = self.default_precision_pair

        if precision.location == HintLocationPrecision.FEATURAL:
            location = region_list.node_from_pickup_index(hint.target)
            debug.debug_print(f"> Choosing location feature for {location}")

            location_features = location.hint_features | region_list.nodes_to_area(location).hint_features
            mean, std_dev = self.location_feature_distribution()
            loc_chooser = self.get_location_feature_chooser(patches, location)

            location_feature = loc_chooser.choose_feature(
                location_features,
                (HintLocationPrecision.REGION_ONLY, HintLocationPrecision.DETAILED),
                rng,
                mean,
                std_dev,
            )

            precision = dataclasses.replace(precision, location=location_feature)

        if precision.item == HintItemPrecision.FEATURAL:
            item = patches.pickup_assignment[hint.target]
            debug.debug_print(f"> Choosing pickup feature for {item.pickup}")

            mean, std_dev = self.item_feature_distribution()
            item_chooser = self.get_pickup_feature_chooser(player_pools)
            item_feature = item_chooser.choose_feature(
                item.pickup.hint_features,
                (HintItemPrecision.DETAILED,),
                rng,
                mean,
                std_dev,
            )
            precision = dataclasses.replace(precision, item=item_feature)

        if precision.include_owner is None:
            owner_chance = 1.0 - (1 / len(player_pools))
            if len(player_pools) > 5:
                owner_chance = 0.0

            include_owner = rng.random() <= owner_chance
            precision = dataclasses.replace(precision, include_owner=include_owner)

        return precision

    def add_hints_precision(
        self,
        player_state: PlayerState,
        patches: GamePatches,
        rng: Random,
        player_pools: Sequence[PlayerPool],
    ) -> GamePatches:
        """
        Adds precision to all hints that are missing one.
        :param player_state:
        :param patches:
        :param rng:
        :return:
        """

        hints_to_replace = {
            identifier: hint for identifier, hint in patches.hints.items() if is_unassigned_location(hint)
        }

        unassigned_hints = list(hints_to_replace.items())
        rng.shuffle(unassigned_hints)

        loc_chooser = self.get_location_feature_chooser(patches)
        loc_chooser.debug_precisions("Location Feature Precisions")

        item_chooser = self.get_pickup_feature_chooser(player_pools)
        item_chooser.debug_precisions("Pickup Feature Precisions")

        # Add random precisions
        for identifier, hint in unassigned_hints:
            precision = self.get_hint_precision(identifier, hint, rng, patches, player_pools)
            hints_to_replace[identifier] = dataclasses.replace(hints_to_replace[identifier], precision=precision)

        # Replace the hints in the patches
        return dataclasses.replace(
            patches,
            hints={identifier: hints_to_replace.get(identifier, hint) for identifier, hint in patches.hints.items()},
        )

    @classmethod
    def location_feature_distribution(cls) -> HintFeatureGaussianParams:
        """
        Params for the precision distribution for location features.
        Override in subclass to fine-tune the balance.
        """
        return 0.85, 0.1

    @classmethod
    def item_feature_distribution(cls) -> HintFeatureGaussianParams:
        """
        Params for the precision distribution for item features.
        Override in subclass to fine-tune the balance.
        """
        return 0.7, 0.2


class AllJokesHintDistributor(HintDistributor):
    @override
    @property
    def default_precision_pair(self) -> PrecisionPair:
        return PRECISION_PAIR_UNASSIGNED

    @override
    async def assign_precision_to_hints(
        self,
        patches: GamePatches,
        rng: Random,
        player_pool: PlayerPool,
        player_state: PlayerState,
        player_pools: list[PlayerPool],
    ) -> GamePatches:
        return self.replace_hints_without_precision_with_jokes(patches)
