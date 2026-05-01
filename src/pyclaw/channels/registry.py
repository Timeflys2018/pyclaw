from __future__ import annotations

from pyclaw.channels.base import ChannelPlugin


class ChannelRegistry:
    def __init__(self) -> None:
        self._plugins: list[ChannelPlugin] = []

    def register(self, plugin: ChannelPlugin) -> None:
        self._plugins.append(plugin)

    async def start_all(self) -> None:
        for plugin in self._plugins:
            await plugin.start()

    async def stop_all(self) -> None:
        for plugin in reversed(self._plugins):
            await plugin.stop()
