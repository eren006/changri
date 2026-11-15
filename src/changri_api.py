"""兼容入口：扩展里写 `import changri_api` / `from changri_api import ...` 照常可用（API v1）。
实际内容在 changri.ext_api；这里直接把模块换成它，两者是同一个对象。"""
import sys

from changri import ext_api as _impl

sys.modules[__name__] = _impl
