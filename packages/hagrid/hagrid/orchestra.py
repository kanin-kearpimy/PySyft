"""Python Level API to launch Docker Containers using Hagrid"""

# future
from __future__ import annotations

# stdlib
from collections.abc import Callable
from enum import Enum
import getpass
import inspect
import os
import subprocess  # nosec
import sys
from threading import Thread
from typing import Any
from typing import TYPE_CHECKING

# relative
from .cli import str_to_bool
from .grammar import find_available_port
from .names import random_name
from .util import ImportFromSyft
from .util import NodeSideType
from .util import shell

DEFAULT_PORT = 8080
DEFAULT_URL = "http://localhost"
# Gevent used instead of threading module ,as we monkey patch gevent in syft
# and this causes context switch error when we use normal threading in hagrid

ClientAlias = Any  # we don't want to import Client in case it changes

if TYPE_CHECKING:
    NodeType = ImportFromSyft.import_node_type()


# Define a function to read and print a stream
def read_stream(stream: subprocess.PIPE) -> None:
    while True:
        line = stream.readline()
        if not line:
            break
        print(line, end="")


def to_snake_case(name: str) -> str:
    return name.lower().replace(" ", "_")


def get_syft_client() -> Any | None:
    try:
        # syft absolute
        import syft as sy

        return sy
    except Exception:  # nosec
        # print("Please install syft with `pip install syft`")
        pass
    return None


def container_exists(name: str) -> bool:
    output = shell(f"docker ps -q -f name='{name}'")
    return len(output) > 0


def port_from_container(name: str, deployment_type: DeploymentType) -> int | None:
    container_suffix = ""
    if deployment_type == DeploymentType.SINGLE_CONTAINER:
        container_suffix = "-worker-1"
    elif deployment_type == DeploymentType.CONTAINER_STACK:
        container_suffix = "-proxy-1"
    else:
        raise NotImplementedError(
            f"port_from_container not implemented for the deployment type:{deployment_type}"
        )

    container_name = name + container_suffix
    output = shell(f"docker port {container_name}")
    if len(output) > 0:
        try:
            # 80/tcp -> 0.0.0.0:8080
            lines = output.split("\n")
            parts = lines[0].split(":")
            port = int(parts[1].strip())
            return port
        except Exception:  # nosec
            return None
    return None


def container_exists_with(name: str, port: int) -> bool:
    output = shell(
        f"docker ps -q -f name={name} | xargs -n 1 docker port | grep 0.0.0.0:{port}"
    )
    return len(output) > 0


def get_node_type(node_type: str | NodeType | None) -> NodeType | None:
    NodeType = ImportFromSyft.import_node_type()
    if node_type is None:
        node_type = os.environ.get("ORCHESTRA_NODE_TYPE", NodeType.DOMAIN)
    try:
        return NodeType(node_type)
    except ValueError:
        print(f"node_type: {node_type} is not a valid NodeType: {NodeType}")
    return None


def get_deployment_type(deployment_type: str | None) -> DeploymentType | None:
    if deployment_type is None:
        deployment_type = os.environ.get(
            "ORCHESTRA_DEPLOYMENT_TYPE", DeploymentType.PYTHON
        )

    # provide shorthands
    if deployment_type == "container":
        deployment_type = "container_stack"

    try:
        return DeploymentType(deployment_type)
    except ValueError:
        print(
            f"deployment_type: {deployment_type} is not a valid DeploymentType: {DeploymentType}"
        )
    return None


# Can also be specified by the environment variable
# ORCHESTRA_DEPLOYMENT_TYPE
class DeploymentType(Enum):
    PYTHON = "python"
    SINGLE_CONTAINER = "single_container"
    CONTAINER_STACK = "container_stack"
    K8S = "k8s"
    PODMAN = "podman"


class NodeHandle:
    def __init__(
        self,
        node_type: NodeType,
        deployment_type: DeploymentType,
        node_side_type: NodeSideType,
        name: str,
        port: int | None = None,
        url: str | None = None,
        python_node: Any | None = None,
        shutdown: Callable | None = None,
    ) -> None:
        self.node_type = node_type
        self.name = name
        self.port = port
        self.url = url
        self.python_node = python_node
        self.shutdown = shutdown
        self.deployment_type = deployment_type
        self.node_side_type = node_side_type

    @property
    def client(self) -> Any:
        if self.port:
            sy = get_syft_client()
            return sy.login_as_guest(url=self.url, port=self.port)  # type: ignore
        elif self.deployment_type == DeploymentType.PYTHON:
            return self.python_node.get_guest_client(verbose=False)  # type: ignore
        else:
            raise NotImplementedError(
                f"client not implemented for the deployment type:{self.deployment_type}"
            )

    def login_as_guest(self, **kwargs: Any) -> ClientAlias:
        return self.client.login_as_guest(**kwargs)

    def login(
        self, email: str | None = None, password: str | None = None, **kwargs: Any
    ) -> ClientAlias:
        if not email:
            email = input("Email: ")

        if not password:
            password = getpass.getpass("Password: ")

        return self.client.login(email=email, password=password, **kwargs)

    def register(
        self,
        name: str,
        email: str | None = None,
        password: str | None = None,
        password_verify: str | None = None,
        institution: str | None = None,
        website: str | None = None,
    ) -> Any:
        SyftError = ImportFromSyft.import_syft_error()
        if not email:
            email = input("Email: ")
        if not password:
            password = getpass.getpass("Password: ")
        if not password_verify:
            password_verify = getpass.getpass("Confirm Password: ")
        if password != password_verify:
            return SyftError(message="Passwords do not match")

        client = self.client
        return client.register(
            name=name,
            email=email,
            password=password,
            institution=institution,
            password_verify=password_verify,
            website=website,
        )

    def land(self) -> None:
        if self.deployment_type == DeploymentType.PYTHON:
            if self.shutdown:
                self.shutdown()
        else:
            Orchestra.land(self.name, deployment_type=self.deployment_type)


def deploy_to_python(
    node_type_enum: NodeType,
    deployment_type_enum: DeploymentType,
    port: int | str,
    name: str,
    host: str,
    reset: bool,
    tail: bool,
    dev_mode: bool,
    processes: int,
    local_db: bool,
    node_side_type: NodeSideType,
    enable_warnings: bool,
    n_consumers: int,
    thread_workers: bool,
    create_producer: bool = False,
    queue_port: int | None = None,
    association_request_auto_approval: bool = False,
) -> NodeHandle | None:
    stage_protocol_changes = ImportFromSyft.import_stage_protocol_changes()
    NodeType = ImportFromSyft.import_node_type()
    sy = get_syft_client()
    if sy is None:
        return sy
    worker_classes = {NodeType.DOMAIN: sy.Domain, NodeType.NETWORK: sy.Gateway}

    # syft >= 0.8.2
    if hasattr(sy, "Enclave"):
        worker_classes[NodeType.ENCLAVE] = sy.Enclave
    if hasattr(NodeType, "GATEWAY"):
        worker_classes[NodeType.GATEWAY] = sy.Gateway

    if dev_mode:
        print("Staging Protocol Changes...")
        stage_protocol_changes()

    kwargs = {
        "name": name,
        "host": host,
        "port": port,
        "reset": reset,
        "processes": processes,
        "dev_mode": dev_mode,
        "tail": tail,
        "node_type": node_type_enum,
        "node_side_type": node_side_type,
        "enable_warnings": enable_warnings,
        # new kwargs
        "queue_port": queue_port,
        "n_consumers": n_consumers,
        "create_producer": create_producer,
        "association_request_auto_approval": association_request_auto_approval,
    }

    if port:
        kwargs["in_memory_workers"] = True
        if port == "auto":
            # dont use default port to prevent port clashes in CI
            port = find_available_port(host="localhost", port=None, search=True)
            kwargs["port"] = port

        sig = inspect.signature(sy.serve_node)
        supported_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}

        start, stop = sy.serve_node(**supported_kwargs)
        start()
        return NodeHandle(
            node_type=node_type_enum,
            deployment_type=deployment_type_enum,
            name=name,
            port=port,
            url="http://localhost",
            shutdown=stop,
            node_side_type=node_side_type,
        )
    else:
        kwargs["local_db"] = local_db
        kwargs["thread_workers"] = thread_workers
        if node_type_enum in worker_classes:
            worker_class = worker_classes[node_type_enum]
            sig = inspect.signature(worker_class.named)
            supported_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
            if "node_type" in sig.parameters.keys() and "migrate" in sig.parameters:
                supported_kwargs["migrate"] = True
            worker = worker_class.named(**supported_kwargs)
        else:
            raise NotImplementedError(f"node_type: {node_type_enum} is not supported")

        def stop() -> None:
            worker.stop()

        return NodeHandle(
            node_type=node_type_enum,
            deployment_type=deployment_type_enum,
            name=name,
            python_node=worker,
            node_side_type=node_side_type,
            shutdown=stop,
        )


def deploy_to_k8s(
    node_type_enum: NodeType,
    deployment_type_enum: DeploymentType,
    name: str,
    node_side_type: NodeSideType,
) -> NodeHandle:
    node_port = int(os.environ.get("NODE_PORT", f"{DEFAULT_PORT}"))
    node_url = str(os.environ.get("NODE_URL", f"{DEFAULT_URL}"))
    return NodeHandle(
        node_type=node_type_enum,
        deployment_type=deployment_type_enum,
        name=name,
        port=node_port,
        url=node_url,
        node_side_type=node_side_type,
    )


def deploy_to_podman(
    node_type_enum: NodeType,
    deployment_type_enum: DeploymentType,
    name: str,
    node_side_type: NodeSideType,
) -> NodeHandle:
    node_port = int(os.environ.get("NODE_PORT", f"{DEFAULT_PORT}"))
    return NodeHandle(
        node_type=node_type_enum,
        deployment_type=deployment_type_enum,
        name=name,
        port=node_port,
        url="http://localhost",
        node_side_type=node_side_type,
    )


def deploy_to_container(
    node_type_enum: NodeType,
    deployment_type_enum: DeploymentType,
    node_side_type: NodeSideType,
    reset: bool,
    cmd: bool,
    tail: bool,
    verbose: bool,
    tag: str,
    render: bool,
    dev_mode: bool,
    port: int | str,
    name: str,
    enable_warnings: bool,
    in_memory_workers: bool,
    association_request_auto_approval: bool = False,
) -> NodeHandle | None:
    if port == "auto" or port is None:
        if container_exists(name=name):
            port = port_from_container(name=name, deployment_type=deployment_type_enum)  # type: ignore
        else:
            port = find_available_port(host="localhost", port=DEFAULT_PORT, search=True)

    # Currently by default we launch in dev mode
    if reset:
        Orchestra.reset(name, deployment_type_enum)
    else:
        if container_exists_with(name=name, port=port):
            return NodeHandle(
                node_type=node_type_enum,
                deployment_type=deployment_type_enum,
                name=name,
                port=port,
                url="http://localhost",
                node_side_type=node_side_type,
            )

    # Start a subprocess and capture its output
    commands = ["hagrid", "launch"]

    name = random_name() if not name else name
    commands.extend([name, node_type_enum.value])

    commands.append("to")
    commands.append(f"docker:{port}")

    if dev_mode:
        commands.append("--dev")

    if not enable_warnings:
        commands.append("--no-warnings")

    if node_side_type.lower() == NodeSideType.LOW_SIDE.value.lower():
        commands.append("--low-side")

    if in_memory_workers:
        commands.append("--in-mem-workers")

    # by default , we deploy as container stack
    if deployment_type_enum == DeploymentType.SINGLE_CONTAINER:
        commands.append("--deployment-type=single_container")

    if association_request_auto_approval:
        commands.append("--enable-association-auto-approval")

    if cmd:
        commands.append("--cmd")

    if tail:
        commands.append("--tail")

    if verbose:
        commands.append("--verbose")

    if tag:
        commands.append(f"--tag={tag}")

    if render:
        commands.append("--render")

    # needed for building containers
    USER = os.environ.get("USER", getpass.getuser())
    env = os.environ.copy()
    env["USER"] = USER

    process = subprocess.Popen(  # nosec
        commands, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    # Start threads to read and print the output and error streams
    stdout_thread = Thread(target=read_stream, args=(process.stdout,))
    stderr_thread = Thread(target=read_stream, args=(process.stderr,))
    # todo, raise errors
    stdout_thread.start()
    stderr_thread.start()
    stdout_thread.join()
    stderr_thread.join()

    if not cmd:
        return NodeHandle(
            node_type=node_type_enum,
            deployment_type=deployment_type_enum,
            name=name,
            port=port,
            url="http://localhost",
            node_side_type=node_side_type,
        )
    return None


class Orchestra:
    @staticmethod
    def launch(
        # node information and deployment
        name: str | None = None,
        node_type: str | NodeType | None = None,
        deploy_to: str | None = None,
        node_side_type: str | None = None,
        # worker related inputs
        port: int | str | None = None,
        processes: int = 1,  # temporary work around for jax in subprocess
        local_db: bool = False,
        dev_mode: bool = False,
        cmd: bool = False,
        reset: bool = False,
        tail: bool = False,
        host: str | None = "0.0.0.0",  # nosec
        tag: str | None = "latest",
        verbose: bool = False,
        render: bool = False,
        enable_warnings: bool = False,
        n_consumers: int = 0,
        thread_workers: bool = False,
        create_producer: bool = False,
        queue_port: int | None = None,
        in_memory_workers: bool = True,
        association_request_auto_approval: bool = False,
    ) -> NodeHandle | None:
        NodeType = ImportFromSyft.import_node_type()
        os.environ["DEV_MODE"] = str(dev_mode)
        if dev_mode is True:
            thread_workers = True

        # syft 0.8.1
        if node_type == "python":
            node_type = NodeType.DOMAIN
            if deploy_to is None:
                deploy_to = "python"

        dev_mode = str_to_bool(os.environ.get("DEV_MODE", f"{dev_mode}"))

        node_type_enum: NodeType | None = get_node_type(node_type=node_type)

        node_side_type_enum = (
            NodeSideType.HIGH_SIDE
            if node_side_type is None
            else NodeSideType(node_side_type)
        )

        deployment_type_enum: DeploymentType | None = get_deployment_type(
            deployment_type=deploy_to
        )
        if not deployment_type_enum:
            return None

        if deployment_type_enum == DeploymentType.PYTHON:
            return deploy_to_python(
                node_type_enum=node_type_enum,
                deployment_type_enum=deployment_type_enum,
                port=port,
                name=name,
                host=host,
                reset=reset,
                tail=tail,
                dev_mode=dev_mode,
                processes=processes,
                local_db=local_db,
                node_side_type=node_side_type_enum,
                enable_warnings=enable_warnings,
                n_consumers=n_consumers,
                thread_workers=thread_workers,
                create_producer=create_producer,
                queue_port=queue_port,
                association_request_auto_approval=association_request_auto_approval,
            )

        elif deployment_type_enum == DeploymentType.K8S:
            return deploy_to_k8s(
                node_type_enum=node_type_enum,
                deployment_type_enum=deployment_type_enum,
                name=name,
                node_side_type=node_side_type_enum,
            )

        elif (
            deployment_type_enum == DeploymentType.CONTAINER_STACK
            or deployment_type_enum == DeploymentType.SINGLE_CONTAINER
        ):
            return deploy_to_container(
                node_type_enum=node_type_enum,
                deployment_type_enum=deployment_type_enum,
                reset=reset,
                cmd=cmd,
                tail=tail,
                verbose=verbose,
                tag=tag,
                render=render,
                dev_mode=dev_mode,
                port=port,
                name=name,
                node_side_type=node_side_type_enum,
                enable_warnings=enable_warnings,
                in_memory_workers=in_memory_workers,
                association_request_auto_approval=association_request_auto_approval,
            )
        elif deployment_type_enum == DeploymentType.PODMAN:
            return deploy_to_podman(
                node_type_enum=node_type_enum,
                deployment_type_enum=deployment_type_enum,
                name=name,
                node_side_type=node_side_type_enum,
            )
        # else:
        #     print(f"deployment_type: {deployment_type_enum} is not supported")
        #     return None

    @staticmethod
    def land(
        name: str, deployment_type: str | DeploymentType, reset: bool = False
    ) -> None:
        deployment_type_enum = DeploymentType(deployment_type)
        Orchestra.shutdown(name=name, deployment_type_enum=deployment_type_enum)
        if reset:
            Orchestra.reset(name, deployment_type_enum=deployment_type_enum)

    @staticmethod
    def shutdown(
        name: str, deployment_type_enum: DeploymentType, reset: bool = False
    ) -> None:
        if deployment_type_enum != DeploymentType.PYTHON:
            snake_name = to_snake_case(name)

            if reset:
                land_output = shell(f"hagrid land {snake_name} --force --prune-vol")
            else:
                land_output = shell(f"hagrid land {snake_name} --force")
            if "Removed" in land_output:
                print(f" ✅ {snake_name} Container Removed")
            elif "No resource found to remove for project" in land_output:
                print(f" ✅ {snake_name} Container does not exist")
            else:
                print(
                    f"❌ Unable to remove container: {snake_name} :{land_output}",
                    file=sys.stderr,
                )

    @staticmethod
    def reset(name: str, deployment_type_enum: DeploymentType) -> None:
        if deployment_type_enum == DeploymentType.PYTHON:
            sy = get_syft_client()
            _ = sy.Worker.named(name=name, processes=1, reset=True)  # type: ignore
        elif (
            deployment_type_enum == DeploymentType.CONTAINER_STACK
            or deployment_type_enum == DeploymentType.SINGLE_CONTAINER
        ):
            Orchestra.shutdown(
                name=name, deployment_type_enum=deployment_type_enum, reset=True
            )
        else:
            raise NotImplementedError(
                f"Reset not implemented for the deployment type:{deployment_type_enum}"
            )
