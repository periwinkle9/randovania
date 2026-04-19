from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

from randovania import monitoring
from randovania.exporter.game_exporter import GameExporter, GameExportParams
from randovania.lib import status_update_lib

if TYPE_CHECKING:
    from randovania.exporter.patch_data_factory import PatcherDataMeta


@dataclasses.dataclass(frozen=True)
class HuntersGameExportParams(GameExportParams):
    input_file: Path
    output_path: Path


class HuntersGameExporter(GameExporter[HuntersGameExportParams]):
    _busy: bool = False

    @property
    def can_start_new_export(self) -> bool:
        """
        Checks if the exporter is busy right now
        """
        return self._busy

    @property
    def export_can_be_aborted(self) -> bool:
        """
        Checks if export_game can be aborted
        """
        return False

    def export_params_type(self) -> type[HuntersGameExportParams]:
        """
        Returns the type of the GameExportParams expected by this exporter.
        """
        return HuntersGameExportParams

    def _do_export_game(
        self,
        patch_data: dict,
        export_params: HuntersGameExportParams,
        progress_update: status_update_lib.ProgressUpdateCallable,
        randovania_meta: PatcherDataMeta,
    ) -> None:

        from open_prime_hunters_rando.version import version as open_prime_hunters_rando_version

        text_patches = patch_data["text_patches"]
        text_patches["patcher_version"] = text_patches["patcher_version"].replace(
            "<version>", f"{open_prime_hunters_rando_version}"
        )

        with monitoring.trace_block("open_prime_hunters_rando.patch_with_status_update"):
            import open_prime_hunters_rando

            open_prime_hunters_rando.patch_with_status_update(
                export_params.input_file,
                export_params.output_path,
                patch_data,
                False,
                lambda progress, msg: progress_update(msg, progress),
            )
