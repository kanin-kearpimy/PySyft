# future
from __future__ import annotations

# stdlib
import ast
import datetime
from enum import Enum
import hashlib
import inspect
from io import StringIO
import itertools
import sys
import time
import traceback
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Type
from typing import Union
from typing import final

# third party
from IPython.display import display
from result import Err
from typing_extensions import Self

# relative
from ...abstract_node import NodeType
from ...client.api import NodeIdentity
from ...client.enclave_client import EnclaveMetadata
from ...node.credentials import SyftVerifyKey
from ...serde.deserialize import _deserialize
from ...serde.serializable import serializable
from ...serde.serialize import _serialize
from ...store.document_store import PartitionKey
from ...store.linked_obj import LinkedObject
from ...types.datetime import DateTime
from ...types.syft_migration import migrate
from ...types.syft_object import SYFT_OBJECT_VERSION_1
from ...types.syft_object import SYFT_OBJECT_VERSION_2
from ...types.syft_object import SYFT_OBJECT_VERSION_3
from ...types.syft_object import SyftHashableObject
from ...types.syft_object import SyftObject
from ...types.transforms import TransformContext
from ...types.transforms import add_node_uid_for_key
from ...types.transforms import drop
from ...types.transforms import generate_id
from ...types.transforms import make_set_default
from ...types.transforms import transform
from ...types.uid import UID
from ...util import options
from ...util.colors import SURFACE
from ...util.markdown import CodeMarkdown
from ...util.markdown import as_markdown_code
from ..action.action_object import Action
from ..action.action_object import ActionObject
from ..context import AuthedServiceContext
from ..dataset.dataset import Asset
from ..job.job_stash import Job
from ..policy.policy import CustomInputPolicy
from ..policy.policy import CustomOutputPolicy
from ..policy.policy import EmpyInputPolicy
from ..policy.policy import ExactMatch
from ..policy.policy import InputPolicy
from ..policy.policy import OutputPolicy
from ..policy.policy import Policy
from ..policy.policy import SingleExecutionExactOutput
from ..policy.policy import SubmitUserPolicy
from ..policy.policy import UserPolicy
from ..policy.policy import init_policy
from ..policy.policy import load_policy_code
from ..policy.policy_service import PolicyService
from ..response import SyftError
from ..response import SyftInfo
from ..response import SyftNotReady
from ..response import SyftSuccess
from ..response import SyftWarning
from .code_parse import GlobalsVisitor
from .code_parse import LaunchJobVisitor
from .unparse import unparse

UserVerifyKeyPartitionKey = PartitionKey(key="user_verify_key", type_=SyftVerifyKey)
CodeHashPartitionKey = PartitionKey(key="code_hash", type_=str)
ServiceFuncNamePartitionKey = PartitionKey(key="service_func_name", type_=str)
SubmitTimePartitionKey = PartitionKey(key="submit_time", type_=DateTime)

PyCodeObject = Any


def extract_uids(kwargs: Dict[str, Any]) -> Dict[str, UID]:
    # relative
    from ...types.twin_object import TwinObject
    from ..action.action_object import ActionObject

    uid_kwargs = {}
    for k, v in kwargs.items():
        uid = v
        if isinstance(v, ActionObject):
            uid = v.id
        if isinstance(v, TwinObject):
            uid = v.id
        if isinstance(v, Asset):
            uid = v.action_id

        if not isinstance(uid, UID):
            raise Exception(f"Input {k} must have a UID not {type(v)}")

        uid_kwargs[k] = uid
    return uid_kwargs


@serializable()
class UserCodeStatus(Enum):
    PENDING = "pending"
    DENIED = "denied"
    APPROVED = "approved"

    def __hash__(self) -> int:
        return hash(self.value)


# User Code status context for multiple approvals
# To make nested dicts hashable for mongodb
# as status is in attr_searchable
@serializable(attrs=["status_dict"])
class UserCodeStatusCollection(SyftHashableObject):
    status_dict: Dict[NodeIdentity, Tuple[UserCodeStatus, str]] = {}

    def __init__(self, status_dict: Dict):
        self.status_dict = status_dict

    def __repr__(self):
        return str(self.status_dict)

    def _repr_html_(self):
        string = f"""
            <style>
                .syft-user_code {{color: {SURFACE[options.color_theme]};}}
                </style>
                <div class='syft-user_code'>
                    <h3 style="line-height: 25%; margin-top: 25px;">User Code Status</h3>
                    <p style="margin-left: 3px;">
            """
        for node_identity, (status, reason) in self.status_dict.items():
            node_name_str = f"{node_identity.node_name}"
            uid_str = f"{node_identity.node_id}"
            status_str = f"{status.value}"
            string += f"""
                    &#x2022; <strong>UID: </strong>{uid_str}&nbsp;
                    <strong>Node name: </strong>{node_name_str}&nbsp;
                    <strong>Status: </strong>{status_str};
                    <strong>Reason: </strong>{reason}
                    <br>
                """
        string += "</p></div>"
        return string

    def __repr_syft_nested__(self):
        string = ""
        for node_identity, (status, reason) in self.status_dict.items():
            string += f"{node_identity.node_name}: {status}, {reason}<br>"
        return string

    def get_status_message(self):
        if self.approved:
            return SyftSuccess(message=f"{type(self)} approved")
        denial_string = ""
        string = ""
        for node_identity, (status, reason) in self.status_dict.items():
            denial_string += f"Code status on node '{node_identity.node_name}' is '{status}'. Reason: {reason}"
            if not reason.endswith("."):
                denial_string += "."
            string += f"Code status on node '{node_identity.node_name}' is '{status}'."
        if self.denied:
            return SyftError(
                message=f"{type(self)} Your code cannot be run: {denial_string}"
            )
        else:
            return SyftNotReady(
                message=f"{type(self)} Your code is waiting for approval. {string}"
            )

    @property
    def approved(self) -> bool:
        return all(x == UserCodeStatus.APPROVED for x, _ in self.status_dict.values())

    @property
    def denied(self) -> bool:
        for status, _ in self.status_dict.values():
            if status == UserCodeStatus.DENIED:
                return True
        return False

    def for_user_context(self, context: AuthedServiceContext) -> UserCodeStatus:
        if context.node.node_type == NodeType.ENCLAVE:
            keys = {status for status, _ in self.status_dict.values()}
            if len(keys) == 1 and UserCodeStatus.APPROVED in keys:
                return UserCodeStatus.APPROVED
            elif UserCodeStatus.PENDING in keys and UserCodeStatus.DENIED not in keys:
                return UserCodeStatus.PENDING
            elif UserCodeStatus.DENIED in keys:
                return UserCodeStatus.DENIED
            else:
                return Exception(f"Invalid types in {keys} for Code Submission")

        elif context.node.node_type == NodeType.DOMAIN:
            node_identity = NodeIdentity(
                node_name=context.node.name,
                node_id=context.node.id,
                verify_key=context.node.signing_key.verify_key,
            )
            if node_identity in self.status_dict:
                return self.status_dict[node_identity][0]
            else:
                raise Exception(
                    f"Code Object does not contain {context.node.name} Domain's data"
                )
        else:
            raise Exception(
                f"Invalid Node Type for Code Submission:{context.node.node_type}"
            )

    def mutate(
        self,
        value: Tuple[UserCodeStatus, str],
        node_name: str,
        node_id,
        verify_key: SyftVerifyKey,
    ) -> Union[SyftError, Self]:
        node_identity = NodeIdentity(
            node_name=node_name, node_id=node_id, verify_key=verify_key
        )
        status_dict = self.status_dict
        if node_identity in status_dict:
            status_dict[node_identity] = value
            self.status_dict = status_dict
            return self
        else:
            return SyftError(
                message="Cannot Modify Status as the Domain's data is not included in the request"
            )


@serializable()
class UserCodeV1(SyftObject):
    # version
    __canonical_name__ = "UserCode"
    __version__ = SYFT_OBJECT_VERSION_1

    id: UID
    node_uid: Optional[UID]
    user_verify_key: SyftVerifyKey
    raw_code: str
    input_policy_type: Union[Type[InputPolicy], UserPolicy]
    input_policy_init_kwargs: Optional[Dict[Any, Any]] = None
    input_policy_state: bytes = b""
    output_policy_type: Union[Type[OutputPolicy], UserPolicy]
    output_policy_init_kwargs: Optional[Dict[Any, Any]] = None
    output_policy_state: bytes = b""
    parsed_code: str
    service_func_name: str
    unique_func_name: str
    user_unique_func_name: str
    code_hash: str
    signature: inspect.Signature
    status: UserCodeStatusCollection
    input_kwargs: List[str]
    enclave_metadata: Optional[EnclaveMetadata] = None
    submit_time: Optional[DateTime]

    __attr_searchable__ = [
        "user_verify_key",
        "status",
        "service_func_name",
        "code_hash",
    ]


@serializable()
class UserCodeV2(SyftObject):
    # version
    __canonical_name__ = "UserCode"
    __version__ = SYFT_OBJECT_VERSION_2

    id: UID
    node_uid: Optional[UID]
    user_verify_key: SyftVerifyKey
    raw_code: str
    input_policy_type: Union[Type[InputPolicy], UserPolicy]
    input_policy_init_kwargs: Optional[Dict[Any, Any]] = None
    input_policy_state: bytes = b""
    output_policy_type: Union[Type[OutputPolicy], UserPolicy]
    output_policy_init_kwargs: Optional[Dict[Any, Any]] = None
    output_policy_state: bytes = b""
    parsed_code: str
    service_func_name: str
    unique_func_name: str
    user_unique_func_name: str
    code_hash: str
    signature: inspect.Signature
    status: UserCodeStatusCollection
    input_kwargs: List[str]
    enclave_metadata: Optional[EnclaveMetadata] = None
    submit_time: Optional[DateTime]
    uses_domain = False  # tracks if the code calls domain.something, variable is set during parsing
    nested_requests: Dict[str, str] = {}
    nested_codes: Optional[Dict[str, Tuple[LinkedObject, Dict]]] = {}


@serializable()
class UserCode(SyftObject):
    # version
    __canonical_name__ = "UserCode"
    __version__ = SYFT_OBJECT_VERSION_3

    id: UID
    node_uid: Optional[UID]
    user_verify_key: SyftVerifyKey
    raw_code: str
    input_policy_type: Union[Type[InputPolicy], UserPolicy]
    input_policy_init_kwargs: Optional[Dict[Any, Any]] = None
    input_policy_state: bytes = b""
    output_policy_type: Union[Type[OutputPolicy], UserPolicy]
    output_policy_init_kwargs: Optional[Dict[Any, Any]] = None
    output_policy_state: bytes = b""
    parsed_code: str
    service_func_name: str
    unique_func_name: str
    user_unique_func_name: str
    code_hash: str
    signature: inspect.Signature
    status: UserCodeStatusCollection
    input_kwargs: List[str]
    enclave_metadata: Optional[EnclaveMetadata] = None
    submit_time: Optional[DateTime]
    uses_domain = False  # tracks if the code calls domain.something, variable is set during parsing
    nested_requests: Dict[str, str] = {}
    nested_codes: Optional[Dict[str, Tuple[LinkedObject, Dict]]] = {}
    worker_pool_id: Optional[UID]

    __attr_searchable__ = [
        "user_verify_key",
        "status",
        "service_func_name",
        "code_hash",
    ]
    __attr_unique__ = []
    __repr_attrs__ = [
        "service_func_name",
        "input_owners",
        "code_status",
        "worker_pool_id",
    ]

    def __setattr__(self, key: str, value: Any) -> None:
        attr = getattr(type(self), key, None)
        if inspect.isdatadescriptor(attr):
            attr.fset(self, value)
        else:
            return super().__setattr__(key, value)

    def _coll_repr_(self) -> Dict[str, Any]:
        status = [status for status, _ in self.status.status_dict.values()][0].value
        if status == UserCodeStatus.PENDING.value:
            badge_color = "badge-purple"
        elif status == UserCodeStatus.APPROVED.value:
            badge_color = "badge-green"
        else:
            badge_color = "badge-red"
        status_badge = {"value": status, "type": badge_color}
        return {
            "Input Policy": self.input_policy_type.__canonical_name__,
            "Output Policy": self.output_policy_type.__canonical_name__,
            "Function name": self.service_func_name,
            "User verify key": {
                "value": str(self.user_verify_key),
                "type": "clipboard",
            },
            "Status": status_badge,
            "Submit time": str(self.submit_time),
        }

    @property
    def is_enclave_code(self) -> bool:
        return self.enclave_metadata is not None

    @property
    def input_owners(self) -> List[str]:
        return [str(x.node_name) for x in self.input_policy_init_kwargs.keys()]

    @property
    def input_owner_verify_keys(self) -> List[SyftVerifyKey]:
        return [x.verify_key for x in self.input_policy_init_kwargs.keys()]

    @property
    def output_reader_names(self) -> List[SyftVerifyKey]:
        keys = self.output_policy_init_kwargs.get("output_readers", [])
        inpkey2name = {x.verify_key: x.node_name for x in self.input_policy_init_kwargs}
        return [inpkey2name[k] for k in keys if k in inpkey2name]

    @property
    def output_readers(self) -> List[SyftVerifyKey]:
        return self.output_policy_init_kwargs.get("output_readers", [])

    @property
    def code_status(self) -> list:
        status_list = []
        for node_view, (status, _) in self.status.status_dict.items():
            status_list.append(
                f"Node: {node_view.node_name}, Status: {status.value}",
            )
        return status_list

    @property
    def input_policy(self) -> Optional[InputPolicy]:
        if not self.status.approved:
            return None

        if len(self.input_policy_state) == 0:
            input_policy = None
            if isinstance(self.input_policy_type, type) and issubclass(
                self.input_policy_type, InputPolicy
            ):
                # TODO: Tech Debt here
                node_view_workaround = False
                for k, _ in self.input_policy_init_kwargs.items():
                    if isinstance(k, NodeIdentity):
                        node_view_workaround = True

                if node_view_workaround:
                    input_policy = self.input_policy_type(
                        init_kwargs=self.input_policy_init_kwargs
                    )
                else:
                    input_policy = self.input_policy_type(
                        **self.input_policy_init_kwargs
                    )
            elif isinstance(self.input_policy_type, UserPolicy):
                input_policy = init_policy(
                    self.input_policy_type, self.input_policy_init_kwargs
                )
            else:
                raise Exception(f"Invalid output_policy_type: {self.input_policy_type}")

            if input_policy is not None:
                input_blob = _serialize(input_policy, to_bytes=True)
                self.input_policy_state = input_blob
                return input_policy
            else:
                raise Exception("input_policy is None during init")
        try:
            return _deserialize(self.input_policy_state, from_bytes=True)
        except Exception as e:
            print(f"Failed to deserialize custom input policy state. {e}")
            return None

    @property
    def output_policy(self) -> Optional[OutputPolicy]:
        if not self.status.approved:
            return None

        if len(self.output_policy_state) == 0:
            output_policy = None
            if isinstance(self.output_policy_type, type) and issubclass(
                self.output_policy_type, OutputPolicy
            ):
                output_policy = self.output_policy_type(
                    **self.output_policy_init_kwargs
                )
            elif isinstance(self.output_policy_type, UserPolicy):
                output_policy = init_policy(
                    self.output_policy_type, self.output_policy_init_kwargs
                )
            else:
                raise Exception(
                    f"Invalid output_policy_type: {self.output_policy_type}"
                )

            if output_policy is not None:
                output_blob = _serialize(output_policy, to_bytes=True)
                self.output_policy_state = output_blob
                return output_policy
            else:
                raise Exception("output_policy is None during init")

        try:
            return _deserialize(self.output_policy_state, from_bytes=True)
        except Exception as e:
            print(f"Failed to deserialize custom output policy state. {e}")
            return None

    @input_policy.setter
    def input_policy(self, value: Any) -> None:
        if isinstance(value, InputPolicy):
            self.input_policy_state = _serialize(value, to_bytes=True)
        elif (isinstance(value, bytes) and len(value) == 0) or value is None:
            self.input_policy_state = b""
        else:
            raise Exception(f"You can't set {type(value)} as input_policy_state")

    @output_policy.setter
    def output_policy(self, value: Any) -> None:
        if isinstance(value, OutputPolicy):
            self.output_policy_state = _serialize(value, to_bytes=True)
        elif (isinstance(value, bytes) and len(value) == 0) or value is None:
            self.output_policy_state = b""
        else:
            raise Exception(f"You can't set {type(value)} as output_policy_state")

    @property
    def byte_code(self) -> Optional[PyCodeObject]:
        return compile_byte_code(self.parsed_code)

    def get_results(self) -> Any:
        # relative
        from ...client.api import APIRegistry

        api = APIRegistry.api_for(self.node_uid, self.syft_client_verify_key)
        return api.services.code.get_results(self)

    @property
    def assets(self) -> List[Asset]:
        # relative
        from ...client.api import APIRegistry

        api = APIRegistry.api_for(self.node_uid, self.syft_client_verify_key)
        if api is None:
            return SyftError(message=f"You must login to {self.node_uid}")

        inputs = (
            uids
            for node_identity, uids in self.input_policy_init_kwargs.items()
            if node_identity.node_name == api.node_name
        )
        all_assets = []
        for uid in itertools.chain.from_iterable(x.values() for x in inputs):
            if isinstance(uid, UID):
                assets = api.services.dataset.get_assets_by_action_id(uid)
                if not isinstance(assets, list):
                    return assets

                all_assets += assets
        return all_assets

    @property
    def unsafe_function(self) -> Optional[Callable]:
        warning = SyftWarning(
            message="This code was submitted by a User and could be UNSAFE."
        )
        display(warning)

        # 🟡 TODO: re-use the same infrastructure as the execute_byte_code function
        def wrapper(*args: Any, **kwargs: Any) -> Callable:
            try:
                filtered_kwargs = {}
                on_private_data, on_mock_data = False, False
                for k, v in kwargs.items():
                    filtered_kwargs[k], arg_type = debox_asset(v)
                    on_private_data = (
                        on_private_data or arg_type == ArgumentType.PRIVATE
                    )
                    on_mock_data = on_mock_data or arg_type == ArgumentType.MOCK

                if on_private_data:
                    display(
                        SyftInfo(
                            message="The result you see is computed on PRIVATE data."
                        )
                    )
                if on_mock_data:
                    display(
                        SyftInfo(message="The result you see is computed on MOCK data.")
                    )

                # remove the decorator
                inner_function = ast.parse(self.raw_code).body[0]
                inner_function.decorator_list = []
                # compile the function
                raw_byte_code = compile_byte_code(unparse(inner_function))
                # load it
                exec(raw_byte_code)  # nosec
                # execute it
                evil_string = f"{self.service_func_name}(**filtered_kwargs)"
                result = eval(evil_string, None, locals())  # nosec
                # return the results
                return result
            except Exception as e:
                print(f"Failed to run unsafe_function. {e}")

        return wrapper

    def _inner_repr(self, level=0):
        shared_with_line = ""
        if len(self.output_readers) > 0:
            owners_string = " and ".join([f"*{x}*" for x in self.output_reader_names])
            shared_with_line += (
                f"Custom Policy: "
                f"outputs are *shared* with the owners of {owners_string} once computed"
            )

        md = f"""class UserCode
    id: UID = {self.id}
    service_func_name: str = {self.service_func_name}
    shareholders: list = {self.input_owners}
    status: list = {self.code_status}
    {shared_with_line}
    code:

{self.raw_code}
"""
        if self.nested_codes != {}:
            md += """

  Nested Requests:
  """

        md = "\n".join(
            [f"{'  '*level}{substring}" for substring in md.split("\n")[:-1]]
        )
        for _, (obj, _) in self.nested_codes.items():
            code = obj.resolve
            md += "\n"
            md += code._inner_repr(level=level + 1)

        return md

    def _repr_markdown_(self):
        return as_markdown_code(self._inner_repr())

    @property
    def show_code(self) -> CodeMarkdown:
        return CodeMarkdown(self.raw_code)

    def show_code_cell(self):
        warning_message = """# WARNING: \n# Before you submit
# change the name of the function \n# for no duplicates\n\n"""

        # third party
        from IPython import get_ipython

        ip = get_ipython()
        ip.set_next_input(warning_message + self.raw_code)


@migrate(UserCode, UserCodeV2)
def downgrade_usercode_v3_to_v2():
    return [
        drop("worker_pool_id"),
    ]


@migrate(UserCodeV2, UserCode)
def upgrade_usercode_v2_to_v3():
    return [
        make_set_default("worker_pool_id", None),
    ]


@serializable(without=["local_function"])
class SubmitUserCode(SyftObject):
    # version
    __canonical_name__ = "SubmitUserCode"
    __version__ = SYFT_OBJECT_VERSION_3

    id: Optional[UID]
    code: str
    func_name: str
    signature: inspect.Signature
    input_policy_type: Union[SubmitUserPolicy, UID, Type[InputPolicy]]
    input_policy_init_kwargs: Optional[Dict[Any, Any]] = {}
    output_policy_type: Union[SubmitUserPolicy, UID, Type[OutputPolicy]]
    output_policy_init_kwargs: Optional[Dict[Any, Any]] = {}
    local_function: Optional[Callable]
    input_kwargs: List[str]
    enclave_metadata: Optional[EnclaveMetadata] = None
    worker_pool_id: Optional[UID] = None

    __repr_attrs__ = ["func_name", "code"]

    @property
    def kwargs(self) -> List[str]:
        return self.input_policy_init_kwargs

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # only run this on the client side
        if self.local_function:
            tree = ast.parse(inspect.getsource(self.local_function))

            # check there are no globals
            v = GlobalsVisitor()
            v.visit(tree)

            # filtered_args = []
            filtered_kwargs = {}
            # for arg in args:
            #     filtered_args.append(debox_asset(arg))
            on_private_data, on_mock_data = False, False
            for k, v in kwargs.items():
                filtered_kwargs[k], arg_type = debox_asset(v)
                on_private_data = on_private_data or arg_type == ArgumentType.PRIVATE
                on_mock_data = on_mock_data or arg_type == ArgumentType.MOCK
            if on_private_data:
                print("Warning: The result you see is computed on PRIVATE data.")
            elif on_mock_data:
                print("Warning: The result you see is computed on MOCK data.")
            return self.local_function(**filtered_kwargs)
        else:
            raise NotImplementedError

    @property
    def input_owner_verify_keys(self) -> List[str]:
        return [x.verify_key for x in self.input_policy_init_kwargs.keys()]


class ArgumentType(Enum):
    REAL = 1
    MOCK = 2
    PRIVATE = 4


def debox_asset(arg: Any) -> Any:
    deboxed_arg = arg
    if isinstance(deboxed_arg, Asset):
        asset = deboxed_arg
        if asset.has_data_permission():
            return asset.data, ArgumentType.PRIVATE
        else:
            return asset.mock, ArgumentType.MOCK
    if hasattr(deboxed_arg, "syft_action_data"):
        deboxed_arg = deboxed_arg.syft_action_data
    return deboxed_arg, ArgumentType.REAL


def syft_function_single_use(
    *args: Any, share_results_with_owners=False, **kwargs: Any
):
    return syft_function(
        input_policy=ExactMatch(*args, **kwargs),
        output_policy=SingleExecutionExactOutput(),
        share_results_with_owners=share_results_with_owners,
    )


def syft_function(
    input_policy: Optional[Union[InputPolicy, UID]] = None,
    output_policy: Optional[Union[OutputPolicy, UID]] = None,
    share_results_with_owners=False,
    worker_pool_id: Optional[Union[UID, str]] = None,
) -> SubmitUserCode:
    if input_policy is None:
        input_policy = EmpyInputPolicy()

    if isinstance(input_policy, CustomInputPolicy):
        input_policy_type = SubmitUserPolicy.from_obj(input_policy)
    else:
        input_policy_type = type(input_policy)

    if output_policy is None:
        output_policy = SingleExecutionExactOutput()

    if isinstance(output_policy, CustomOutputPolicy):
        output_policy_type = SubmitUserPolicy.from_obj(output_policy)
    else:
        output_policy_type = type(output_policy)

    if isinstance(worker_pool_id, str):
        worker_pool_id = UID(worker_pool_id)

    def decorator(f):
        res = SubmitUserCode(
            code=inspect.getsource(f),
            func_name=f.__name__,
            signature=inspect.signature(f),
            input_policy_type=input_policy_type,
            input_policy_init_kwargs=input_policy.init_kwargs,
            output_policy_type=output_policy_type,
            output_policy_init_kwargs=output_policy.init_kwargs,
            local_function=f,
            input_kwargs=f.__code__.co_varnames[: f.__code__.co_argcount],
            worker_pool_id=worker_pool_id,
        )

        if share_results_with_owners:
            res.output_policy_init_kwargs[
                "output_readers"
            ] = res.input_owner_verify_keys

        success_message = SyftSuccess(
            message=f"Syft function '{f.__name__}' successfully created. "
            f"To add a code request, please create a project using `project = syft.Project(...)`, "
            f"then use command `project.create_code_request`."
        )
        display(success_message)

        return res

    return decorator


def generate_unique_func_name(context: TransformContext) -> TransformContext:
    code_hash = context.output["code_hash"]
    service_func_name = context.output["func_name"]
    context.output["service_func_name"] = service_func_name
    func_name = f"user_func_{service_func_name}_{context.credentials}_{code_hash}"
    user_unique_func_name = (
        f"user_func_{service_func_name}_{context.credentials}_{time.time()}"
    )
    context.output["unique_func_name"] = func_name
    context.output["user_unique_func_name"] = user_unique_func_name
    return context


def process_code(
    context,
    raw_code: str,
    func_name: str,
    original_func_name: str,
    policy_input_kwargs: List[str],
    function_input_kwargs: List[str],
) -> str:
    tree = ast.parse(raw_code)

    # check there are no globals
    v = GlobalsVisitor()
    v.visit(tree)

    f = tree.body[0]
    f.decorator_list = []

    call_args = function_input_kwargs
    if "domain" in function_input_kwargs:
        context.output["uses_domain"] = True
    call_stmt_keywords = [ast.keyword(arg=i, value=[ast.Name(id=i)]) for i in call_args]
    call_stmt = ast.Assign(
        targets=[ast.Name(id="result")],
        value=ast.Call(
            func=ast.Name(id=original_func_name), args=[], keywords=call_stmt_keywords
        ),
        lineno=0,
    )

    return_stmt = ast.Return(value=ast.Name(id="result"))
    new_body = tree.body + [call_stmt, return_stmt]

    wrapper_function = ast.FunctionDef(
        name=func_name,
        args=f.args,
        body=new_body,
        decorator_list=[],
        returns=None,
        lineno=0,
    )

    return unparse(wrapper_function)


def new_check_code(context: TransformContext) -> TransformContext:
    # TODO remove this tech debt hack
    input_kwargs = context.output["input_policy_init_kwargs"]
    node_view_workaround = False
    for k in input_kwargs.keys():
        if isinstance(k, NodeIdentity):
            node_view_workaround = True

    if not node_view_workaround:
        input_keys = list(input_kwargs.keys())
    else:
        input_keys = []
        for d in input_kwargs.values():
            input_keys += d.keys()

    processed_code = process_code(
        context,
        raw_code=context.output["raw_code"],
        func_name=context.output["unique_func_name"],
        original_func_name=context.output["service_func_name"],
        policy_input_kwargs=input_keys,
        function_input_kwargs=context.output["input_kwargs"],
    )
    context.output["parsed_code"] = processed_code

    return context


def locate_launch_jobs(context: TransformContext) -> TransformContext:
    # stdlib
    nested_requests = {}
    tree = ast.parse(context.output["raw_code"])

    # look for domain arg
    if "domain" in [arg.arg for arg in tree.body[0].args.args]:
        v = LaunchJobVisitor()
        v.visit(tree)
        nested_calls = v.nested_calls
        for call in nested_calls:
            nested_requests[call] = "latest"

    context.output["nested_requests"] = nested_requests
    return context


def compile_byte_code(parsed_code: str) -> Optional[PyCodeObject]:
    try:
        return compile(parsed_code, "<string>", "exec")
    except Exception as e:
        print("WARNING: to compile byte code", e)
    return None


def compile_code(context: TransformContext) -> TransformContext:
    byte_code = compile_byte_code(context.output["parsed_code"])
    if byte_code is None:
        raise Exception(
            "Unable to compile byte code from parsed code. "
            + context.output["parsed_code"]
        )
    return context


def hash_code(context: TransformContext) -> TransformContext:
    code = context.output["code"]
    context.output["raw_code"] = code
    code_hash = hashlib.sha256(code.encode("utf8")).hexdigest()
    context.output["code_hash"] = code_hash
    return context


def add_credentials_for_key(key: str) -> Callable:
    def add_credentials(context: TransformContext) -> TransformContext:
        context.output[key] = context.credentials
        return context

    return add_credentials


def check_policy(policy: Policy, context: TransformContext) -> TransformContext:
    policy_service = context.node.get_service(PolicyService)
    if isinstance(policy, SubmitUserPolicy):
        policy = policy.to(UserPolicy, context=context)
    elif isinstance(policy, UID):
        policy = policy_service.get_policy_by_uid(context, policy)
        if policy.is_ok():
            policy = policy.ok()

    return policy


def check_input_policy(context: TransformContext) -> TransformContext:
    ip = context.output["input_policy_type"]
    ip = check_policy(policy=ip, context=context)
    context.output["input_policy_type"] = ip
    return context


def check_output_policy(context: TransformContext) -> TransformContext:
    op = context.output["output_policy_type"]
    op = check_policy(policy=op, context=context)
    context.output["output_policy_type"] = op
    return context


def add_custom_status(context: TransformContext) -> TransformContext:
    input_keys = list(context.output["input_policy_init_kwargs"].keys())
    if context.node.node_type == NodeType.DOMAIN:
        node_identity = NodeIdentity(
            node_name=context.node.name,
            node_id=context.node.id,
            verify_key=context.node.signing_key.verify_key,
        )
        context.output["status"] = UserCodeStatusCollection(
            status_dict={node_identity: (UserCodeStatus.PENDING, "")}
        )
        # if node_identity in input_keys or len(input_keys) == 0:
        #     context.output["status"] = UserCodeStatusContext(
        #         base_dict={node_identity: UserCodeStatus.SUBMITTED}
        #     )
        # else:
        #     raise ValueError(f"Invalid input keys: {input_keys} for {node_identity}")
    elif context.node.node_type == NodeType.ENCLAVE:
        status_dict = {key: (UserCodeStatus.PENDING, "") for key in input_keys}
        context.output["status"] = UserCodeStatusCollection(status_dict=status_dict)
    else:
        raise NotImplementedError(
            f"Invalid node type:{context.node.node_type} for code submission"
        )
    return context


def add_submit_time(context: TransformContext) -> TransformContext:
    context.output["submit_time"] = DateTime.now()
    return context


def set_default_pool_if_empty(context: TransformContext) -> TransformContext:
    if context.output.get("worker_pool_id", None) is None:
        default_pool = context.node.get_default_worker_pool()
        context.output["worker_pool_id"] = default_pool.id
    return context


@transform(SubmitUserCode, UserCode)
def submit_user_code_to_user_code() -> List[Callable]:
    return [
        generate_id,
        hash_code,
        generate_unique_func_name,
        check_input_policy,
        check_output_policy,
        new_check_code,
        locate_launch_jobs,
        add_credentials_for_key("user_verify_key"),
        add_custom_status,
        add_node_uid_for_key("node_uid"),
        add_submit_time,
        set_default_pool_if_empty,
    ]


@serializable()
class UserCodeExecutionResult(SyftObject):
    # version
    __canonical_name__ = "UserCodeExecutionResult"
    __version__ = SYFT_OBJECT_VERSION_1

    id: UID
    user_code_id: UID
    stdout: str
    stderr: str
    result: Any


class SecureContext:
    def __init__(self, context):
        node = context.node
        job_service = node.get_service("jobservice")
        action_service = node.get_service("actionservice")
        # user_service = node.get_service("userservice")

        def job_set_n_iters(n_iters):
            job = context.job
            job.n_iters = n_iters
            job_service.update(context, job)

        def job_set_current_iter(current_iter):
            job = context.job
            job.current_iter = current_iter
            job_service.update(context, job)

        def job_increase_current_iter(current_iter):
            job = context.job
            job.current_iter += current_iter
            job_service.update(context, job)

        # def set_api_registry():
        #     user_signing_key = [
        #         x.signing_key
        #         for x in user_service.stash.partition.data.values()
        #         if x.verify_key == context.credentials
        #     ][0]
        #     data_protcol = get_data_protocol()
        #     user_api = node.get_api(context.credentials, data_protcol.latest_version)
        #     user_api.signing_key = user_signing_key
        #     # We hardcode a python connection here since we have access to the node
        #     # TODO: this is not secure
        #     user_api.connection = PythonConnection(node=node)

        #     APIRegistry.set_api_for(
        #         node_uid=node.id,
        #         user_verify_key=context.credentials,
        #         api=user_api,
        #     )

        def launch_job(func: UserCode, **kwargs):
            # relative

            kw2id = {}
            for k, v in kwargs.items():
                value = ActionObject.from_obj(v)
                ptr = action_service.set(context, value)
                ptr = ptr.ok()
                kw2id[k] = ptr.id
            try:
                # TODO: check permissions here
                action = Action.syft_function_action_from_kwargs_and_id(kw2id, func.id)

                job = node.add_action_to_queue(
                    action=action,
                    credentials=context.credentials,
                    parent_job_id=context.job_id,
                    has_execute_permissions=True,
                    worker_pool_id=func.worker_pool_id,
                )
                # # set api in global scope to enable using .get(), .wait())
                # set_api_registry()

                return job
            except Exception as e:
                print(f"ERROR {e}")
                raise ValueError(f"error while launching job:\n{e}")

        self.job_set_n_iters = job_set_n_iters
        self.job_set_current_iter = job_set_current_iter
        self.job_increase_current_iter = job_increase_current_iter
        self.launch_job = launch_job
        self.is_async = context.job is not None


def execute_byte_code(
    code_item: UserCode, kwargs: Dict[str, Any], context: AuthedServiceContext
) -> Any:
    stdout_ = sys.stdout
    stderr_ = sys.stderr

    try:
        # stdlib
        import builtins as __builtin__

        original_print = __builtin__.print

        safe_context = SecureContext(context=context)

        class LocalDomainClient:
            def init_progress(self, n_iters):
                if safe_context.is_async:
                    safe_context.job_set_current_iter(0)
                    safe_context.job_set_n_iters(n_iters)

            def set_progress(self, to) -> None:
                self._set_progress(to)

            def increment_progress(self, n=1) -> None:
                self._set_progress(by=n)

            def _set_progress(self, to=None, by=None):
                if safe_context.is_async is not None:
                    if by is None and to is None:
                        by = 1
                    if to is None:
                        safe_context.job_increase_current_iter(current_iter=by)
                    else:
                        safe_context.job_set_current_iter(to)

            @final
            def launch_job(self, func: UserCode, **kwargs):
                return safe_context.launch_job(func, **kwargs)

            def __setattr__(self, __name: str, __value: Any) -> None:
                raise Exception("Attempting to alter read-only value")

        if context.job is not None:
            job_id = context.job_id
            log_id = context.job.log_id

            def print(*args, sep=" ", end="\n"):
                def to_str(arg: Any) -> str:
                    if isinstance(arg, bytes):
                        return arg.decode("utf-8")
                    if isinstance(arg, Job):
                        return f"JOB: {arg.id}"
                    if isinstance(arg, SyftError):
                        return f"JOB: {arg.message}"
                    if isinstance(arg, ActionObject):
                        return str(arg.syft_action_data)
                    return str(arg)

                new_args = [to_str(arg) for arg in args]
                new_str = sep.join(new_args) + end
                log_service = context.node.get_service("LogService")
                log_service.append(context=context, uid=log_id, new_str=new_str)
                time = datetime.datetime.now().strftime("%d/%m/%y %H:%M:%S")
                return __builtin__.print(
                    f"{time} FUNCTION LOG ({job_id}):",
                    *new_args,
                    end=end,
                    sep=sep,
                    file=sys.stderr,
                )

        else:
            print = original_print

        if code_item.uses_domain:
            kwargs["domain"] = LocalDomainClient()

        stdout = StringIO()
        stderr = StringIO()

        # statisfy lint checker
        result = None

        # We only need access to local kwargs
        _locals = {"kwargs": kwargs}
        _globals = {}

        for service_func_name, (linked_obj, _) in code_item.nested_codes.items():
            code_obj = linked_obj.resolve_with_context(context=context)
            if isinstance(code_obj, Err):
                raise Exception(code_obj.err())
            _globals[service_func_name] = code_obj.ok()
        _globals["print"] = print
        exec(code_item.parsed_code, _globals, _locals)  # nosec

        evil_string = f"{code_item.unique_func_name}(**kwargs)"
        try:
            result = eval(evil_string, _globals, _locals)  # nosec
        except Exception as e:
            if context.job is not None:
                error_msg = traceback_from_error(e, code_item)
                time = datetime.datetime.now().strftime("%d/%m/%y %H:%M:%S")
                original_print(
                    f"{time} EXCEPTION LOG ({job_id}):\n{error_msg}", file=sys.stderr
                )
                log_service = context.node.get_service("LogService")
                log_service.append(context=context, uid=log_id, new_err=error_msg)
            result = Err(
                f"Exception encountered while running {code_item.service_func_name}"
                ", please contact the Node Admin for more info."
            )

        # reset print
        print = original_print

        # restore stdout and stderr
        sys.stdout = stdout_
        sys.stderr = stderr_

        return UserCodeExecutionResult(
            user_code_id=code_item.id,
            stdout=str(stdout.getvalue()),
            stderr=str(stderr.getvalue()),
            result=result,
        )

    except Exception as e:
        # stdlib

        print = original_print
        # print("execute_byte_code failed", e, file=stderr_)
        print(traceback.format_exc())
        print("execute_byte_code failed", e)
    finally:
        sys.stdout = stdout_
        sys.stderr = stderr_


def traceback_from_error(e, code: UserCode):
    """We do this because the normal traceback.format_exc() does not work well for exec,
    it missed the references to the actual code"""
    line_nr = 0
    tb = e.__traceback__
    while tb is not None:
        line_nr = tb.tb_lineno - 1
        tb = tb.tb_next

    lines = code.parsed_code.split("\n")
    start_line = max(0, line_nr - 2)
    end_line = min(len(lines), line_nr + 2)
    error_lines: str = [
        e.replace("   ", f"    {i} ", 1)
        if i != line_nr
        else e.replace("   ", f"--> {i} ", 1)
        for i, e in enumerate(lines)
        if i >= start_line and i < end_line
    ]
    error_lines = "\n".join(error_lines)

    error_msg = f"""
Encountered while executing {code.service_func_name}:
{traceback.format_exc()}
{error_lines}"""
    return error_msg


def load_approved_policy_code(user_code_items: List[UserCode]) -> Any:
    """Reload the policy code in memory for user code that is approved."""
    try:
        for user_code in user_code_items:
            if user_code.status.approved:
                if isinstance(user_code.input_policy_type, UserPolicy):
                    load_policy_code(user_code.input_policy_type)
                if isinstance(user_code.output_policy_type, UserPolicy):
                    load_policy_code(user_code.output_policy_type)
    except Exception as e:
        raise Exception(f"Failed to load code: {user_code}: {e}")
