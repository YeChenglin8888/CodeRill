import asyncio
import sys

from coderill.runtime import ProcessManager


def test_process_reports_output_and_exit_code(tmp_path):
    result = asyncio.run(ProcessManager().run("echo coderill", tmp_path, 10, asyncio.Event()))
    assert result.status == "success"
    assert result.returncode == 0
    assert "coderill" in result.output


def test_nonzero_exit_is_failure(tmp_path):
    command = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
    result = asyncio.run(ProcessManager().run(command, tmp_path, 10, asyncio.Event()))
    assert result.status == "failed"
    assert result.returncode == 3


def test_process_timeout_stops_the_command(tmp_path):
    command = f'"{sys.executable}" -c "import time; time.sleep(10)"'
    result = asyncio.run(ProcessManager().run(command, tmp_path, 1, asyncio.Event()))
    assert result.status == "timeout"


def test_process_cancel_stops_the_command(tmp_path):
    async def scenario():
        cancel = asyncio.Event()
        command = f'"{sys.executable}" -c "import time; time.sleep(10)"'
        task = asyncio.create_task(ProcessManager().run(command, tmp_path, 20, cancel))
        await asyncio.sleep(0.1)
        cancel.set()
        return await task

    assert asyncio.run(scenario()).status == "interrupted"
