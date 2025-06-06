from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from randovania.games.fusion.layout import FusionConfiguration
from randovania.resolver.bootstrap import Bootstrap
from randovania.resolver.energy_tank_damage_state import EnergyTankDamageState

if TYPE_CHECKING:
    from random import Random

    from randovania.game_description.db.pickup_node import PickupNode
    from randovania.game_description.game_description import GameDescription
    from randovania.game_description.game_patches import GamePatches
    from randovania.game_description.resources.resource_collection import ResourceCollection
    from randovania.game_description.resources.resource_database import ResourceDatabase
    from randovania.generator.pickup_pool import PoolResults
    from randovania.resolver.damage_state import DamageState


def is_metroid_location(node: PickupNode, config: FusionConfiguration) -> bool:
    _boss_indices = [100, 106, 114, 104, 115, 107, 110, 102, 109, 108, 111]
    artifact_config = config.artifacts
    index = node.pickup_index.index
    return artifact_config.prefer_bosses and index in _boss_indices


class FusionBootstrap(Bootstrap[FusionConfiguration]):
    def create_damage_state(self, game: GameDescription, configuration: FusionConfiguration) -> DamageState:
        return EnergyTankDamageState(
            configuration.energy_per_tank - 1,
            configuration.energy_per_tank,
            game.resource_database,
            game.region_list,
        )

    def _get_enabled_misc_resources(
        self, configuration: FusionConfiguration, resource_database: ResourceDatabase
    ) -> set[str]:
        enabled_resources = set()
        if configuration.dock_rando.is_enabled():
            enabled_resources.add("DoorLockRando")
        enabled_resources.add("BomblessPBs")
        enabled_resources.add("GeneratorHack")
        return enabled_resources

    def _damage_reduction(self, db: ResourceDatabase, current_resources: ResourceCollection) -> float:
        num_suits = sum(
            (1 if current_resources[db.get_item_by_name(suit)] else 0) for suit in ("Varia Suit", "Gravity Suit")
        )
        dr = 1.0
        if num_suits == 1:
            dr = 0.9
        elif num_suits >= 2:
            dr = 0.8

        return dr

    def patch_resource_database(self, db: ResourceDatabase, configuration: FusionConfiguration) -> ResourceDatabase:
        return dataclasses.replace(db, base_damage_reduction=self._damage_reduction)

    def assign_pool_results(
        self, rng: Random, configuration: FusionConfiguration, patches: GamePatches, pool_results: PoolResults
    ) -> GamePatches:
        if configuration.artifacts.prefer_anywhere:
            return super().assign_pool_results(rng, configuration, patches, pool_results)

        pickups_to_preplace = [
            pickup for pickup in list(pool_results.to_place) if pickup.gui_category.name == "InfantMetroid"
        ]
        locations = self.all_preplaced_pickup_locations(patches.game, configuration, is_metroid_location)
        self.pre_place_pickups(rng, pickups_to_preplace, locations, pool_results, patches.game.game)
        return super().assign_pool_results(rng, configuration, patches, pool_results)
