"""Every command the driver interfaces declare: its request model, its response model, and how a driver is called with it.

Read from the interface classes with `inspect` and `typing.get_type_hints`. Adding a command means adding the abstract
method and its models; nothing else lists commands.
"""

import inspect
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import NoneType
from typing import Any, get_type_hints

from pydantic import BaseModel, JsonValue, create_model

from cheshire_drivers import interfaces as _interfaces
from cheshire_drivers.command_responses import EmptyCommandResponse
from cheshire_drivers.liquid_handler_models import _StrictModel
from cheshire_drivers.driver_introspection import (
    CommandFunction, declared_response, interface_command_names, interface_member_names, is_external,
    is_lifecycle,
)

DriverKwargs = dict[str, BaseModel | JsonValue]


@dataclass(frozen=True)
class DeviceCommand:
    interface: str
    name: str
    request_model: type[BaseModel]
    request_param: str | None
    """The method's one model argument, or None when the request model was built from the method's own arguments."""
    response_model: type[BaseModel]
    is_property: bool
    function: CommandFunction
    external: bool
    """Marked `@external` on its interface: an operator or a workflow may send it. Otherwise only the engine does."""
    lifecycle: bool
    """Marked `@lifecycle` on its interface: only its own operation sends it, never a workflow or `execute`."""

    def validate(self, params: Mapping[str, JsonValue]) -> BaseModel:
        return self.request_model.model_validate(dict(params))

    def driver_kwargs(self, params: Mapping[str, JsonValue]) -> DriverKwargs:
        request = self.validate(params)
        if self.request_param is not None:
            return {self.request_param: request}
        return {name: getattr(request, name) for name in type(request).model_fields}


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def _request_of(interface: str, name: str, function: CommandFunction) -> tuple[type[BaseModel], str | None]:
    # With extras, so a limit an interface puts on a plain argument reaches the request model.
    hints = get_type_hints(function, include_extras=True)
    parameters = [p for p in inspect.signature(function).parameters.values() if p.name != "self"]
    if len(parameters) == 1:
        annotation = hints.get(parameters[0].name)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation, parameters[0].name
    # create_model takes its fields as **kwargs: Any.
    fields: dict[str, Any] = {p.name: (hints[p.name], ... if p.default is inspect.Parameter.empty else p.default) for p in parameters}
    return create_model(f"{interface}{_camel(name)}Request", __base__=_StrictModel, **fields), None


def _response_of(interface: str, name: str, member: property | CommandFunction, function: CommandFunction) -> type[BaseModel]:
    declared = declared_response(member)
    if declared is not None:
        return declared
    returned = get_type_hints(function).get("return", NoneType)
    if returned is NoneType:
        return EmptyCommandResponse
    if isinstance(returned, type) and issubclass(returned, BaseModel):
        return returned
    raise TypeError(f"{interface}.{name} returns {returned!r}; mark it @returns(<response model>).")


def _commands_of(interface: str, cls: type) -> dict[str, DeviceCommand]:
    commands: dict[str, DeviceCommand] = {}
    invokable = interface_command_names(cls)
    for name in sorted(interface_member_names(cls)):
        member = inspect.getattr_static(cls, name)
        if isinstance(member, property):
            if declared_response(member) is None or member.fget is None:
                continue
            function: CommandFunction = member.fget
        elif name in invokable:
            function = getattr(cls, name)
        else:
            continue
        request_model, request_param = _request_of(interface, name, function)
        commands[name] = DeviceCommand(
            interface=interface,
            name=name,
            request_model=request_model,
            request_param=request_param,
            response_model=_response_of(interface, name, member, function),
            is_property=isinstance(member, property),
            function=function,
            external=is_external(member),
            lifecycle=is_lifecycle(member),
        )
    return commands


def _interface_classes() -> dict[str, type]:
    return {
        name.removesuffix("Driver"): cls
        for name, cls in vars(_interfaces).items()
        if isinstance(cls, type)
        and name.startswith("I")
        and name.endswith("Driver")
        and cls.__module__ == _interfaces.__name__
    }


def _same_command(a: DeviceCommand, b: DeviceCommand) -> bool:
    """One function, or two interfaces declaring the same arguments and answer under one name."""
    if a.function is b.function:
        return True
    return (
        a.is_property == b.is_property
        and a.request_param == b.request_param
        and a.response_model is b.response_model
        and a.request_model.model_json_schema().get("properties") == b.request_model.model_json_schema().get("properties")
    )


class CommandTable:
    def __init__(self, interface_classes: Mapping[str, type]) -> None:
        self._classes = dict(interface_classes)
        self._commands = {interface: _commands_of(interface, cls) for interface, cls in self._classes.items()}
        every = [c for commands in self._commands.values() for c in commands.values()]
        self._external = frozenset(c.name for c in every if c.external)
        self._lifecycle = frozenset(c.name for c in every if c.lifecycle)

    def interface(self, name: str) -> type | None:
        return self._classes.get(name)

    def interface_names(self) -> frozenset[str]:
        return frozenset(self._classes)

    def commands_of(self, interface: str) -> Mapping[str, DeviceCommand]:
        return self._commands.get(interface, {})

    def external_names(self) -> frozenset[str]:
        """Every command an operator or a workflow may send, on any interface."""
        return self._external

    def lifecycle_names(self) -> frozenset[str]:
        """Every command only its own operation sends, on any interface."""
        return self._lifecycle

    def find(self, interfaces: Iterable[str], name: str) -> DeviceCommand | None:
        """The command a device with these interfaces runs under this name, or None for a vendor extra."""
        found = [self._commands[i][name] for i in sorted(interfaces) if name in self._commands.get(i, {})]
        distinct: list[DeviceCommand] = []
        for command in found:
            if not any(_same_command(command, kept) for kept in distinct):
                distinct.append(command)
        if len(distinct) <= 1:
            return distinct[0] if distinct else None
        base = getattr(_interfaces.BaseDriver, name, None)
        overrides = [command for command in distinct if command.function is not base]
        if len(overrides) == 1:
            return overrides[0]
        raise ValueError(f"{name!r} is a different command on each of {sorted(c.interface for c in distinct)}.")

    def response_model(self, interfaces: Iterable[str], name: str) -> type[BaseModel]:
        """The command's response model; a vendor extra answers with an empty response."""
        command = self.find(interfaces, name)
        return EmptyCommandResponse if command is None else command.response_model


COMMANDS = CommandTable(_interface_classes())
