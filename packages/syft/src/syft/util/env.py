from ..types.syft_object import SyftObject, SYFT_OBJECT_VERSION_1
from typing import Dict
import venv

class Env(SyftObject):
    __canonical_name__ = "Env"
    __version__ = SYFT_OBJECT_VERSION_1
    packages_dict: Dict[str, str]
    
    @property
    def packages(self):
        return [(k, v) for k, v in self.packages_dict.items()]
    
    def create_local_env(self):
        venv.EnvBuilder()