"""单店电商后端；API、worker 与 relay 共用业务层。"""

import tomllib
from pathlib import Path

# 项目按源码/镜像分发；Dockerfile 同样将 pyproject.toml 放在 /app。
__version__ = str(
    tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())["project"][
        "version"
    ]
)
