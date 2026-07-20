from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.modules.products.factory import get_product_service


spec = spec_from_file_location("bld_matcher_web", Path(__file__).with_name("app.py"))
module = module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)

app = module.app
get_product_service().warm_catalog()
