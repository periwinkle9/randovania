from __future__ import annotations

import json
import typing
from typing import Any

import construct
from construct import CString, Flag, If, PascalString, PrefixedArray, Rebuild, Struct, VarInt

String = PascalString(VarInt, "utf-8")


def varint_emitbuild(code: construct.CodeGen) -> str:
    code.append("""
    def _varint_build(obj: int, io, this):
        x = obj
        B = bytearray()
        while x > 0b01111111:
            B.append(0b10000000 | (x & 0b01111111))
            x >>= 7
        B.append(x)
        io.write(bytes(B))
        return obj
    """)
    return "_varint_build(obj, io, this)"


def zigzag_emitbuild(code: construct.CodeGen) -> str:
    varint_emitbuild(code)
    code.append("""
    def _zigzag_build(obj: int, io, this):
        if obj >= 0:
            x = 2*obj
        else:
            x = 2*abs(obj)-1
        _varint_build(x, io, this)
        return obj
    """)
    return "_zigzag_build(obj, io, this)"


def add_compile_support_to_construct() -> None:
    """Modify construct types to have support for compiling"""

    construct.VarInt._emitbuild = varint_emitbuild
    construct.ZigZag._emitbuild = zigzag_emitbuild


def convert_to_raw_python(value: Any) -> Any:
    if isinstance(value, list):
        return [convert_to_raw_python(item) for item in value]

    if isinstance(value, dict):
        return {key: convert_to_raw_python(item) for key, item in value.items() if not key.startswith("_")}

    if isinstance(value, construct.EnumIntegerString):
        return str(value)

    return value


def is_path_not_equals_to(path: construct.Path, value: typing.Any) -> construct.BinExpr:
    return path != value


def OptionalValue(subcon: construct.Construct) -> construct.Construct:
    return construct.FocusedSeq(
        "value",
        present=Rebuild(Flag, is_path_not_equals_to(construct.this.value, None)),
        value=If(construct.this.present, subcon),
    )


class DictAdapter(construct.Adapter):
    def _decode(self, obj: construct.ListContainer, context: construct.Container, path: str) -> construct.Container:
        result: construct.Container = construct.Container()
        last = {}
        for i, item in enumerate(obj):
            key = item.key
            if key in result:
                raise construct.ConstructError(f"Key {key} found twice in object", path)
            last[key] = i
            result[key] = item.value
        return result

    def _encode(self, obj: construct.Container, context: construct.Container, path: str) -> construct.ListContainer:
        return construct.ListContainer(construct.Container(key=type_, value=item) for type_, item in obj.items())


def ConstructDict(subcon: construct.Construct) -> construct.Construct:
    return DictAdapter(
        PrefixedArray(
            VarInt,
            Struct(
                key=String,
                value=subcon,
            ),
        )
    )


class JsonEncodedValueAdapter(construct.Adapter):
    def _decode(self, obj: str, context: construct.Container, path: str) -> typing.Any:
        return json.loads(obj)

    def _encode(self, obj: typing.Any, context: construct.Container, path: str) -> str:
        return json.dumps(obj, separators=(",", ":"))


JsonEncodedValue = JsonEncodedValueAdapter(String)
CompressedJsonValue = construct.Prefixed(construct.VarInt, construct.Compressed(JsonEncodedValue, "zlib"))
NullTerminatedCompressedJsonValue = construct.Prefixed(
    construct.VarInt, construct.Compressed(JsonEncodedValueAdapter(CString("utf-8")), "zlib")
)
add_compile_support_to_construct()
