"""启动/停止脚本的端口筛选回归（Windows findstr 真实行为）。"""
import re
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not __import__("shutil").which("findstr"), reason="需要 Windows findstr")
def test_stop_script_pid_filter_only_matches_workbench_listener():
    bat = Path(__file__).resolve().parents[2] / "停止服务.bat"
    text = bat.read_bytes().decode("gbk")
    match = re.search(r'findstr\s+/r\s+/i\s+"([^"]+)"', text)
    assert match, "停止脚本必须包含端口筛选正则"
    pattern = match.group(1)

    netstat_output = (
        "  TCP    127.0.0.1:23333       0.0.0.0:0              LISTENING       25076\n"
        "  TCP    127.0.0.1:8000        0.0.0.0:0              LISTENING       28032\n"
        "  TCP    127.0.0.1:49687       0.0.0.0:0              LISTENING       7076\n"
    )
    result = subprocess.run(
        ["findstr", "/r", "/i", pattern],
        input=netstat_output,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    matched = result.stdout.strip().splitlines()
    assert len(matched) == 1, "findstr 正则不能含空格，否则空格会分隔成 OR 搜索串"
    assert ":8000 " in matched[0]
    assert matched[0].rstrip().endswith("28032")
