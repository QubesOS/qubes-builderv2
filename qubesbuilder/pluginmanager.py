import importlib.util
import sys
from collections import OrderedDict
from pathlib import Path
from typing import List, Dict

from qubesbuilder.exc import EntityError, PluginManagerError
from qubesbuilder.log import QubesBuilderLogger
from qubesbuilder.plugins import Plugin


class PluginEntity:
    def __init__(self, path: Path):
        self.path = path
        self.directory = path.parent
        # Determine plugin name based on directory or filename
        if path.name == "__init__.py":
            self.name = self.directory.name
        else:
            self.name = path.name.replace(".py", "")
        # If exists, Remove qubes- prefix
        if self.name.startswith("qubes-"):
            self.name = self.name[6:]
        # Replace - by _
        self.name = self.name.replace("-", "_")
        self.fullname = f"qubesbuilder.plugins.{self.name}"

        try:
            spec = importlib.util.spec_from_file_location(
                self.fullname, self.path
            )
            if not spec:
                raise EntityError("Cannot get module spec.")
            self.module = importlib.util.module_from_spec(spec)
            if not spec.loader:
                raise EntityError("Cannot get module from spec.")
            sys.modules[self.fullname] = self.module
            spec.loader.exec_module(self.module)
        except ImportError as e:
            raise EntityError(str(e)) from e


class PluginManager:
    def __init__(self, directories: List[Path]):
        self._directories = directories
        self._entities: Dict[str, PluginEntity] = {}
        self._log = QubesBuilderLogger.getChild("pluginmanager")

    def _get_plugin_entities(self):
        entities = OrderedDict()
        for directory in self._directories:
            directory_path = Path(directory).expanduser().resolve()
            if not directory_path.exists():
                self._log.warning(
                    f"Ignoring non existing directory '{directory_path}'. If directory is"
                    f" a component plugin, component source may not be fetched."
                )
                continue
            modules = directory_path.iterdir()
            for module in modules:
                if module.is_dir():
                    module_path = module / "__init__.py"
                    if not module_path.exists():
                        continue
                    entity = PluginEntity(module_path)
                elif module.name.endswith(".py"):
                    entity = PluginEntity(module)
                else:
                    continue
                # Ensure module name are uniq
                if entity.name in entities:
                    raise PluginManagerError(
                        f"Conflicting module name detected: '{entity.name}'."
                    )
                entities[entity.name] = entity

        return entities

    def _get_plugins_with_attr(self, module_attr) -> List[Plugin]:
        # Ensure plugin class name are uniq
        plugin_names = []
        for entity in self.entities.values():
            if not hasattr(entity.module, module_attr):
                continue
            plugin_names += [
                c.__name__ for c in getattr(entity.module, module_attr)
            ]

        if len(set(plugin_names)) != len(plugin_names):
            raise PluginManagerError("Conflicting plugin name detected.")

        plugins = []
        for entity in self.entities.values():
            if not hasattr(entity.module, module_attr):
                continue
            plugins += getattr(entity.module, module_attr)
        return plugins

    @property
    def entities(self):
        if not self._entities:
            self._entities = self._get_plugin_entities()
        return self._entities

    def get_plugins(self) -> List[Plugin]:
        return self._get_plugins_with_attr("PLUGINS")
