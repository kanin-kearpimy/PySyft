# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: proto/lib/petlib/ec_obj.proto
"""Generated protocol buffer code."""
# third party
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf import reflection as _reflection
from google.protobuf import symbol_database as _symbol_database

# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()


DESCRIPTOR = _descriptor.FileDescriptor(
    name="proto/lib/petlib/ec_obj.proto",
    package="syft.lib.petlib",
    syntax="proto3",
    serialized_options=None,
    create_key=_descriptor._internal_create_key,
    serialized_pb=b'\n\x1dproto/lib/petlib/ec_obj.proto\x12\x0fsyft.lib.petlib"<\n\x07\x45\x63Pt_PB\x12\x11\n\tgroup_nid\x18\x01 \x01(\x04\x12\x10\n\x08obj_type\x18\x02 \x01(\t\x12\x0c\n\x04\x64\x61ta\x18\x03 \x01(\x0c\x62\x06proto3',
)


_ECPT_PB = _descriptor.Descriptor(
    name="EcPt_PB",
    full_name="syft.lib.petlib.EcPt_PB",
    filename=None,
    file=DESCRIPTOR,
    containing_type=None,
    create_key=_descriptor._internal_create_key,
    fields=[
        _descriptor.FieldDescriptor(
            name="group_nid",
            full_name="syft.lib.petlib.EcPt_PB.group_nid",
            index=0,
            number=1,
            type=4,
            cpp_type=4,
            label=1,
            has_default_value=False,
            default_value=0,
            message_type=None,
            enum_type=None,
            containing_type=None,
            is_extension=False,
            extension_scope=None,
            serialized_options=None,
            file=DESCRIPTOR,
            create_key=_descriptor._internal_create_key,
        ),
        _descriptor.FieldDescriptor(
            name="obj_type",
            full_name="syft.lib.petlib.EcPt_PB.obj_type",
            index=1,
            number=2,
            type=9,
            cpp_type=9,
            label=1,
            has_default_value=False,
            default_value=b"".decode("utf-8"),
            message_type=None,
            enum_type=None,
            containing_type=None,
            is_extension=False,
            extension_scope=None,
            serialized_options=None,
            file=DESCRIPTOR,
            create_key=_descriptor._internal_create_key,
        ),
        _descriptor.FieldDescriptor(
            name="data",
            full_name="syft.lib.petlib.EcPt_PB.data",
            index=2,
            number=3,
            type=12,
            cpp_type=9,
            label=1,
            has_default_value=False,
            default_value=b"",
            message_type=None,
            enum_type=None,
            containing_type=None,
            is_extension=False,
            extension_scope=None,
            serialized_options=None,
            file=DESCRIPTOR,
            create_key=_descriptor._internal_create_key,
        ),
    ],
    extensions=[],
    nested_types=[],
    enum_types=[],
    serialized_options=None,
    is_extendable=False,
    syntax="proto3",
    extension_ranges=[],
    oneofs=[],
    serialized_start=50,
    serialized_end=110,
)

DESCRIPTOR.message_types_by_name["EcPt_PB"] = _ECPT_PB
_sym_db.RegisterFileDescriptor(DESCRIPTOR)

EcPt_PB = _reflection.GeneratedProtocolMessageType(
    "EcPt_PB",
    (_message.Message,),
    {
        "DESCRIPTOR": _ECPT_PB,
        "__module__": "proto.lib.petlib.ec_obj_pb2"
        # @@protoc_insertion_point(class_scope:syft.lib.petlib.EcPt_PB)
    },
)
_sym_db.RegisterMessage(EcPt_PB)


# @@protoc_insertion_point(module_scope)
