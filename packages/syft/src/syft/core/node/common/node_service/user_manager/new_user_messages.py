# stdlib
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Type
from typing import Union

# third party
from nacl.encoding import HexEncoder
from nacl.signing import SigningKey
from nacl.signing import VerifyKey
from pydantic import EmailStr
from typing_extensions import final

# relative
from .....common.serde.serializable import serializable
from ....abstract.node_service_interface import NodeServiceInterface
from ....domain.domain_interface import DomainInterface
from ....domain.registry import DomainMessageRegistry
from ...exceptions import AuthorizationError
from ...exceptions import MissingRequestKeyError
from ...exceptions import UserNotFoundError
from ...node_table.utils import model_to_json
from ...permissions.permissions import BasePermission
from ...permissions.user_permissions import UserCanCreateUsers
from ...permissions.user_permissions import UserCanTriageRequest
from ..generic_payload.syft_message import NewSyftMessage as SyftMessage
from ..generic_payload.syft_message import ReplyPayload
from ..generic_payload.syft_message import RequestPayload


@serializable(recursive_serde=True)
@final
class CreateUserMessage(SyftMessage, DomainMessageRegistry):

    # Pydantic Inner class to define expected request payload fields.
    class Request(RequestPayload):
        """Payload fields and types used during a User Creation Request."""

        email: EmailStr
        password: str
        name: str
        role: Optional[str] = "Data Scientist"
        institution: Optional[str]
        website: Optional[str]
        budget: Optional[float] = 0.0
        daa_pdf: Optional[bytes] = b""

    # Pydantic Inner class to define expected reply payload fields.
    class Reply(ReplyPayload):
        """Payload fields and types used during a User Creation Response."""

        message: str = "User created successfully!"

    request_payload_type = (
        Request  # Converts generic syft dict into a CreateUserMessage.Request object.
    )
    reply_payload_type = (
        Reply  # Creates a proper Reply payload message structure as a response.
    )

    def run(  # type: ignore
        self, node: DomainInterface, verify_key: Optional[VerifyKey] = None
    ) -> Union[List, Dict[str, Any]]:
        # Check key permissions
        if node.setup.first(domain_name=node.name).daa and not self.payload.daa_pdf:
            raise AuthorizationError(
                message="You can't apply a new User without a DAA document!"
            )

        # Check if email/password fields are empty
        if not self.payload.email or not self.payload.password:
            raise MissingRequestKeyError(
                message="Invalid request payload, empty fields (email/password)!"
            )

        # Check if this email was already registered
        try:
            node.users.first(email=self.payload.email)
            # If the email has already been registered, raise exception
            raise AuthorizationError(
                message="You can't create a new User using this email!"
            )
        except UserNotFoundError:
            # If email not registered, a new user can be created.
            pass

        app_id = node.users.create_user_application(
            name=self.payload.name,
            email=self.payload.email,
            password=self.payload.password,
            daa_pdf=self.payload.daa_pdf,
            institution=self.payload.institution,
            website=self.payload.website,
            budget=self.payload.budget,
        )

        user_role_id = -1
        try:
            user_role_id = node.users.role(verify_key=verify_key).id
        except Exception as e:
            print("verify_key not in db", e)

        if node.roles.can_create_users(role_id=user_role_id):
            node.users.process_user_application(
                candidate_id=app_id, status="accepted", verify_key=verify_key
            )

        return CreateUserMessage.Reply()


@serializable(recursive_serde=True)
@final
class GetUserMessage(SyftMessage, DomainMessageRegistry):

    # Pydantic Inner class to define expected request payload fields.
    class Request(RequestPayload):
        user_id: int

    # Pydantic Inner class to define expected reply payload fields.
    class Reply(ReplyPayload):
        id: int
        name: str
        email: str
        role: Union[int, str]
        budget: float
        created_at: str
        budget_spent: Optional[float] = 0.0
        institution: Optional[str] = ""
        website: Optional[str] = ""
        added_by: Optional[str] = ""

    request_payload_type = Request
    reply_payload_type = Reply

    def run(
        self, node: NodeServiceInterface, verify_key: Optional[VerifyKey] = None
    ) -> Union[None, ReplyPayload]:
        # Retrieve User Model
        user = node.users.first(id=self.payload.user_id)  # type: ignore

        # Build Reply
        reply = GetUserMessage.Reply(**model_to_json(user))

        # Use role name instead of role ID.
        reply.role = node.roles.first(id=reply.role).name  # type: ignore

        # Get budget spent
        reply.budget_spent = node.acc.user_budget(  # type: ignore
            user_key=VerifyKey(user.verify_key.encode("utf-8"), encoder=HexEncoder)
        )
        return reply

    def get_permissions(self) -> List[Type[BasePermission]]:
        return [UserCanTriageRequest]


@serializable(recursive_serde=True)
@final
class GetUsersMessage(SyftMessage, DomainMessageRegistry):

    # Pydantic Inner class to define expected request payload fields.
    class Request(RequestPayload):
        pass

    # Pydantic Inner class to define expected reply payload fields.
    class Reply(ReplyPayload):
        users: List[GetUserMessage.Reply] = []

    request_payload_type = Request
    reply_payload_type = Reply

    def run(  # type: ignore
        self, node: NodeServiceInterface, verify_key: Optional[VerifyKey] = None
    ) -> Union[None, ReplyPayload]:
        # Get All Users
        users = node.users.all()
        users_list = list()
        for user in users:
            user_model = GetUserMessage.Reply(**model_to_json(user))

            # Use role name instead of role ID.
            user_model.role = node.roles.first(id=user_model.role).name

            # Remaining Budget
            # TODO:
            # Rename it from budget_spent to remaining budget
            user_model.budget_spent = node.acc.get_remaining_budget(  # type: ignore
                user_key=VerifyKey(user.verify_key.encode("utf-8"), encoder=HexEncoder),
                returned_epsilon_is_private=False,
            )
            users_list.append(user_model)

        reply = GetUsersMessage.Reply.construct()
        reply.users = users_list
        return reply

    def get_permissions(self) -> List:
        return [UserCanTriageRequest]


@serializable(recursive_serde=True)
@final
class DeleteUserMessage(SyftMessage, DomainMessageRegistry):

    # Pydantic Inner class to define expected request payload fields.
    class Request(RequestPayload):
        user_id: int

    # Pydantic Inner class to define expected reply payload fields.
    class Reply(ReplyPayload):
        message: str = "User deleted successfully!"

    request_payload_type = Request
    reply_payload_type = Reply

    def run(  # type: ignore
        self, node: NodeServiceInterface, verify_key: Optional[VerifyKey] = None
    ) -> Union[None, ReplyPayload]:

        _target_user = node.users.first(id=self.payload.user_id)
        _not_owner = (
            node.roles.first(id=_target_user.role).name != node.roles.owner_role.name
        )

        if _not_owner:
            node.users.delete(id=self.payload.user_id)
        else:
            raise AuthorizationError(
                "You're not allowed to delete this user information!"
            )

        return DeleteUserMessage.Reply()

    def get_permissions(self) -> List[Type[BasePermission]]:
        return [UserCanCreateUsers]


@serializable(recursive_serde=True)
@final
class UpdateUserMessage(SyftMessage, DomainMessageRegistry):

    # Pydantic Inner class to define expected request payload fields.
    class Request(RequestPayload):
        user_id: int
        name: Optional[str] = ""
        email: Optional[EmailStr] = ""
        institution: Optional[str] = ""
        website: Optional[str] = ""
        password: Optional[str] = ""
        role: Optional[str] = ""

    # Pydantic Inner class to define expected reply payload fields.
    class Reply(ReplyPayload):
        message: str = "User updated successfully!"

    request_payload_type = Request
    reply_payload_type = Reply

    def run(  # type: ignore
        self, node: NodeServiceInterface, verify_key: Optional[VerifyKey] = None
    ) -> Union[None, ReplyPayload]:

        _valid_parameters = (
            self.payload.email
            or self.payload.password
            or self.payload.role
            or self.payload.name
            or self.payload.institution
            or self.payload.website
        )

        # Change own information
        if self.payload.user_id == 0:
            self.payload.user_id = int(node.users.get_user(verify_key).id)  # type: ignore

        _valid_user = node.users.contain(id=self.payload.user_id)

        if not _valid_parameters:
            raise MissingRequestKeyError(
                "Missing json fields ( email,password,role,groups, name )"
            )

        if not _valid_user:
            raise UserNotFoundError

        # Change Institution
        if self.payload.institution:
            node.users.set(
                user_id=str(self.payload.user_id), institution=self.payload.institution
            )

        # Change Website
        if self.payload.website:
            node.users.set(
                user_id=str(self.payload.user_id), website=self.payload.website
            )

        # Change Email Request
        elif self.payload.email:
            node.users.set(user_id=str(self.payload.user_id), email=self.payload.email)

        # Change Password Request
        elif self.payload.password:
            node.users.set(
                user_id=str(self.payload.user_id), password=self.payload.password
            )

        # Change Name Request
        elif self.payload.name:
            node.users.set(user_id=str(self.payload.user_id), name=self.payload.name)

        # Change Role Request
        elif self.payload.role:
            target_user = node.users.first(id=self.payload.user_id)
            _allowed = (
                self.payload.role != node.roles.owner_role.name  # Target Role != Owner
                and target_user.role
                != node.roles.owner_role.id  # Target User Role != Owner
                and node.users.can_create_users(
                    verify_key=verify_key
                )  # Key Permissions
            )

            # If all premises were respected
            if _allowed:
                new_role_id = node.roles.first(name=self.payload.role).id
                node.users.set(user_id=self.payload.user_id, role=new_role_id)  # type: ignore
            elif (  # Transfering Owner's role
                self.payload.role == node.roles.owner_role.name  # target role == Owner
                and node.users.role(verify_key=verify_key).name
                == node.roles.owner_role.name  # Current user is the current node owner.
            ):
                new_role_id = node.roles.first(name=self.payload.role).id
                node.users.set(user_id=str(self.payload.user_id), role=new_role_id)
                current_user = node.users.get_user(verify_key=verify_key)
                node.users.set(user_id=current_user.id, role=node.roles.admin_role.id)  # type: ignore
                # Updating current node keys
                root_key = SigningKey(
                    current_user.private_key.encode("utf-8"), encoder=HexEncoder  # type: ignore
                )
                node.signing_key = root_key
                node.verify_key = root_key.verify_key
                # IDK why, but we also have a different var (node.root_verify_key)
                # defined at ...common.node.py that points to the verify_key.
                # So we need to update it as well.
                node.root_verify_key = root_key.verify_key
            elif target_user.role == node.roles.owner_role.id:
                raise AuthorizationError(
                    "You're not allowed to change Owner user roles!"
                )
            else:
                raise AuthorizationError("You're not allowed to change User roles!")

        return UpdateUserMessage.Reply()

    def get_permissions(self) -> List[Type[BasePermission]]:
        return [UserCanCreateUsers]
