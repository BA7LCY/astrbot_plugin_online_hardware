from .nand import NandProvider
from .sandisk import SandiskProvider
from .seagate import SeagateProvider
from .toshiba import ToshibaProvider
from .western_digital import WesternDigitalProvider
from .ymtc import YmtcProvider


def build_providers(timeout: int = 15):
    providers = [
        WesternDigitalProvider(timeout),
        SeagateProvider(timeout),
        ToshibaProvider(timeout),
        SandiskProvider(timeout),
        YmtcProvider(timeout),
    ]
    return {provider.brand_id: provider for provider in providers}


def build_nand_provider(timeout: int = 15, api_base: str = "") -> NandProvider:
    return NandProvider(timeout, api_base)
