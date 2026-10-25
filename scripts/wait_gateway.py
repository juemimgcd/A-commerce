"""滚动发布时读取网关的实际后端状态；只读接口，不改变网关配置。"""

import argparse
import csv
import io
import time
from urllib.error import URLError
from urllib.request import urlopen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("server", choices=["api", "api-secondary"])
    parser.add_argument("status", choices=["UP", "DOWN"])
    args = parser.parse_args()
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            with urlopen("http://gateway:8081/stats;csv", timeout=3) as response:
                rows = csv.DictReader(io.StringIO(response.read().decode()))
                for row in rows:
                    if (
                        row.get("# pxname") == "public_api"
                        and row.get("svname") == args.server
                        and row.get("status") == args.status
                    ):
                        print(f"网关确认 {args.server}={args.status}")
                        return
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(1)
    raise SystemExit(f"网关未在期限内确认 {args.server}={args.status}；停止滚动发布。")


if __name__ == "__main__":
    main()
