# stdlib
from textwrap import dedent
import uuid

# third party
from faker import Faker
import numpy as np

# syft absolute
import syft as sy
from syft.client.api import APIRegistry
from syft.service.action.action_object import ActionObject
from syft.service.request.request import Request
from syft.service.request.request import UserCodeStatusChange
from syft.service.response import SyftError
from syft.service.user.user import User


@sy.syft_function(
    input_policy=sy.ExactMatch(), output_policy=sy.SingleExecutionExactOutput()
)
def test_func():
    return 1


@sy.syft_function(
    input_policy=sy.ExactMatch(), output_policy=sy.SingleExecutionExactOutput()
)
def test_func_2():
    return 1


def test_user_code(worker, guest_client: User) -> None:
    test_func()
    guest_client.api.services.code.request_code_execution(test_func)

    root_domain_client = worker.root_client
    message = root_domain_client.notifications[-1]
    request = message.link
    user_code = request.changes[0].link
    result = user_code.unsafe_function()
    request.accept_by_depositing_result(result)

    result = guest_client.api.services.code.test_func()
    assert isinstance(result, ActionObject)

    real_result = result.get()
    assert isinstance(real_result, int)


def test_duplicated_user_code(worker, guest_client: User) -> None:
    test_func()
    result = guest_client.api.services.code.request_code_execution(test_func)
    assert isinstance(result, Request)
    assert len(guest_client.code.get_all()) == 1

    # request the exact same code should return an error
    result = guest_client.api.services.code.request_code_execution(test_func)
    assert isinstance(result, SyftError)
    assert len(guest_client.code.get_all()) == 1

    # request the a different function name but same content will also succeed
    test_func_2()
    result = guest_client.api.services.code.request_code_execution(test_func_2)
    assert isinstance(result, Request)
    assert len(guest_client.code.get_all()) == 2


def random_hash() -> str:
    return uuid.uuid4().hex[:16]


def test_scientist_can_list_code_assets(worker: sy.Worker, faker: Faker) -> None:
    asset_name = random_hash()
    asset = sy.Asset(
        name=asset_name, data=np.array([1, 2, 3]), mock=sy.ActionObject.empty()
    )
    dataset_name = random_hash()
    dataset = sy.Dataset(name=dataset_name, asset_list=[asset])

    root_client = worker.root_client

    password = random_hash()
    credentials = {
        "name": faker.name(),
        "email": faker.email(),
        "password": password,
        "password_verify": password,
    }

    root_client.register(**credentials)

    guest_client = root_client.guest()
    credentials.pop("name")
    guest_client = guest_client.login(**credentials)

    root_client.upload_dataset(dataset=dataset)

    asset_input = root_client.datasets.search(name=dataset_name)[0].asset_list[0]

    @sy.syft_function_single_use(asset=asset_input)
    def func(asset):
        return 0

    func.code = dedent(func.code)

    request = guest_client.code.request_code_execution(func)
    assert not isinstance(request, sy.SyftError)

    status_change = next(
        c for c in request.changes if (isinstance(c, UserCodeStatusChange))
    )

    assert status_change.linked_obj.resolve.assets[0] == asset_input


@sy.syft_function()
def test_inner_func():
    return 1


@sy.syft_function(
    input_policy=sy.ExactMatch(), output_policy=sy.SingleExecutionExactOutput()
)
def test_outer_func(domain):
    job = domain.launch_job(test_inner_func)
    return job


def test_nested_requests(worker, guest_client: User):
    guest_client.api.services.code.submit(test_inner_func)
    guest_client.api.services.code.request_code_execution(test_outer_func)

    root_domain_client = worker.root_client
    request = root_domain_client.requests[-1]
    assert request.code.nested_requests == {"test_inner_func": "latest"}
    root_domain_client.api.services.request.apply(request.id)
    request = root_domain_client.requests[-1]

    codes = root_domain_client.code
    inner = codes[0] if codes[0].service_func_name == "test_inner_func" else codes[1]
    outer = codes[0] if codes[0].service_func_name == "test_outer_func" else codes[1]
    assert list(request.code.nested_codes.keys()) == ["test_inner_func"]
    (linked_obj, node) = request.code.nested_codes["test_inner_func"]
    assert node == {}
    assert linked_obj.resolve.id == inner.id
    assert outer.status.approved
    assert not inner.status.approved


def test_local_execution(worker):
    root_domain_client = worker.root_client
    dataset = sy.Dataset(
        name="test",
        asset_list=[
            sy.Asset(
                name="test",
                data=np.array([1, 2, 3]),
                mock=np.array([1, 1, 1]),
            )
        ],
    )
    root_domain_client.upload_dataset(dataset)
    asset = root_domain_client.datasets[0].assets[0]

    APIRegistry.set_api_for(
        node_uid=worker.id,
        user_verify_key=root_domain_client.verify_key,
        api=root_domain_client.api,
    )

    @sy.syft_function(
        input_policy=sy.ExactMatch(x=asset),
        output_policy=sy.SingleExecutionExactOutput(),
    )
    def my_func(x):
        return x + 1

    my_func.code = dedent(my_func.code)

    local_res = my_func(x=asset, time_alive=1)
    assert (local_res == np.array([2, 2, 2])).all()
