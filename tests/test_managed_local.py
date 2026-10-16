"""Exercise the desktop bridge contract against real loopback listeners."""

import asyncio
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import ClientSession, web
import server


class ManagedLocalTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_players_qr_and_clean_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            stop_path = Path(directory) / "stop"
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(("127.0.0.1", 0))
                dsu_port = probe.getsockname()[1]
            app = web.Application()
            app.update(hub=server.Hub(), backend="dolphin", system=server.get_system("wii"),
                       state_file=state_path, local_url=f"http://127.0.0.1:{port}/")
            app.router.add_get("/ws", server.ws_handler)
            app.on_startup.append(server.start_background)
            app.on_cleanup.append(server.cleanup_background)
            server.write_runtime_state(app)
            self.assertFalse(state_path.exists())
            with patch.object(server, "DSU_PORT", dsu_port):
                task = asyncio.create_task(server.run_managed_local(
                    app, "127.0.0.1", port, None, stop_path))
                try:
                    async with asyncio.timeout(5):
                        while not state_path.exists():
                            if task.done():
                                await task
                            await asyncio.sleep(.02)
                    state = json.loads(state_path.read_text())
                    self.assertTrue(state["ready"])
                    self.assertEqual(state["dsu_port"], dsu_port)
                    self.assertIn("<svg", state_path.with_suffix(".svg").read_text())
                    async with ClientSession() as client:
                        async with client.ws_connect(f"http://127.0.0.1:{port}/ws?client=abcdefghijklmnop") as ws:
                            self.assertEqual((await ws.receive_json())["player"], 1)
                            await asyncio.sleep(.35)
                            self.assertEqual(json.loads(state_path.read_text())["players"], [{"player": 1}])
                        await asyncio.sleep(.35)
                        self.assertEqual(json.loads(state_path.read_text())["players"], [])
                        async with client.ws_connect(f"http://127.0.0.1:{port}/ws?client=abcdefghijklmnop") as ws:
                            self.assertEqual((await ws.receive_json())["player"], 1)
                            stop_path.touch()
                            await asyncio.wait_for(task, 3)
                finally:
                    stop_path.touch()
                    await asyncio.wait_for(task, 5)
            self.assertFalse(state_path.exists())
            self.assertFalse(state_path.with_suffix(".svg").exists())
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(("127.0.0.1", dsu_port))

    async def test_failed_web_bind_never_reports_ready_and_releases_dsu(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(("127.0.0.1", 0))
                dsu_port = probe.getsockname()[1]
            app = web.Application()
            state = Path(directory) / "state.json"
            app.update(hub=server.Hub(), backend="dolphin", state_file=state,
                       local_url="http://127.0.0.1/")
            app.on_startup.append(server.start_background)
            app.on_cleanup.append(server.cleanup_background)
            with patch.object(server, "DSU_PORT", dsu_port):
                with self.assertRaises(OSError):
                    await server.run_managed_local(app, "127.0.0.1", occupied.getsockname()[1],
                                                   None, Path(directory) / "stop")
            self.assertFalse(state.exists())
            await asyncio.sleep(0)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(("127.0.0.1", dsu_port))
