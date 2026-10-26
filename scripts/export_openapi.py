"""导出真实 FastAPI 契约供前端生成类型，不连接数据库或启动后台任务。"""

import json
from pathlib import Path

from a_commerce.main import create_app


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "frontend" / "openapi.json"
    target.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n")
    print(f"OpenAPI exported: {target}")


if __name__ == "__main__":
    main()
