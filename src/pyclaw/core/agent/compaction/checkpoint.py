from __future__ import annotations

from pyclaw.models import SessionTree


class CompactionCheckpoint:
    def __init__(self, tree: SessionTree) -> None:
        self._snapshot = tree.model_copy(deep=True)

    def restore_into(self, tree: SessionTree) -> None:
        tree.entries.clear()
        tree.entries.update(
            {k: v.model_copy(deep=True) for k, v in self._snapshot.entries.items()}
        )
        tree.order = list(self._snapshot.order)
        tree.leaf_id = self._snapshot.leaf_id

    @property
    def snapshot(self) -> SessionTree:
        return self._snapshot


def take_checkpoint(tree: SessionTree) -> CompactionCheckpoint:
    return CompactionCheckpoint(tree)
