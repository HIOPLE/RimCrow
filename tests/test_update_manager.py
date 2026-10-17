import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from backend.managers.mgr_download import DownloadTask
from backend.managers.mgr_update import GiteeSource, GithubSource, LanzouSource, LocalSource, ManifestSource, SourceForgeSource, UpdateInfo, UpdateManager, UpdateSourceError, _build_configured_update_sources
from backend.settings import settings
from backend.utils.lanzou_parser import LanzouParser


class StubUpdateSource:
    def __init__(self, info):
        self.info = info

    def check(self):
        return self.info


class FailingUpdateSource:
    def check(self):
        raise UpdateSourceError("更新源不可用")


class StubDownloadManager:
    def __init__(self):
        self.tasks = []

    def add_task(self, **kwargs):
        self.tasks.append(kwargs)
        return f"task-{len(self.tasks)}"


class TestGithubUpdateSource(unittest.TestCase):
    def test_check_routes_release_asset_through_selected_mirror_with_direct_fallback(self):
        source = GithubSource("Inky-Feather/RimCrow")
        release = {
            "tag_name": "v0.22.7",
            "draft": False,
            "prerelease": False,
            "assets": [{
                "name": "RimCrow-v0.22.7-windows.zip",
                "browser_download_url": "https://github.com/Inky-Feather/RimCrow/releases/download/v0.22.7/app.zip",
                "digest": "sha256:" + "a" * 64,
            }],
        }
        with patch("backend.managers.mgr_update.__version__", "0.22.6"), \
             patch("backend.utils.tools.platform.system", return_value="Windows"), \
             patch.object(source.github_mgr, "fetch_release", return_value=release), \
             patch.object(settings.config, "github_mirror_id", "custom:custom"), \
             patch.object(settings.config, "github_mirrors", [{"id": "custom", "url_template": "https://mirror.example/{url}", "support": {"release_asset": {"status": "supported"}}}]):
            info = source.check()

        self.assertEqual(info.download_url, "https://mirror.example/https://github.com/Inky-Feather/RimCrow/releases/download/v0.22.7/app.zip")
        self.assertEqual(info.fallback_urls, ["https://github.com/Inky-Feather/RimCrow/releases/download/v0.22.7/app.zip"])

    def test_check_returns_github_release_asset_update(self):
        source = GithubSource("Inky-Feather/RimCrow")
        releases = [
            {
                "tag_name": "v0.22.7",
                "name": "v0.22.7",
                "draft": False,
                "prerelease": False,
                "body": "修复问题",
                "published_at": "2026-06-25T00:00:00Z",
                "assets": [
                    {"name": "source.zip", "browser_download_url": "https://example.invalid/source.zip", "size": 10},
                    {
                        "name": "RimCrow-v0.22.7-windows.zip",
                        "browser_download_url": "https://example.invalid/app.zip",
                        "size": 67227285,
                        "digest": "sha256:abc123",
                    },
                ],
            }
        ]

        with patch("backend.managers.mgr_update.__version__", "0.22.6"), \
            patch("backend.utils.tools.platform.system", return_value="Windows"), \
            patch.object(source.github_mgr, "fetch_release", return_value=releases[0]):
            info = source.check()

        self.assertIsNotNone(info)
        self.assertEqual(info.version, "0.22.7")
        self.assertEqual(info.download_url, "https://example.invalid/app.zip")
        self.assertEqual(info.source_name, "GitHub")
        self.assertEqual(info.file_hash, "abc123")
        self.assertEqual(info.hash_algorithm, "sha256")
        self.assertEqual(info.file_size, "64.1 MB")

    def test_download_uses_github_candidates_in_one_task(self):
        source = GithubSource("Inky-Feather/RimCrow")
        release = {
            "tag_name": "v0.22.7",
            "draft": False,
            "prerelease": False,
            "assets": [{
                "name": "RimCrow-v0.22.7-windows.zip",
                "browser_download_url": "https://github.com/Inky-Feather/RimCrow/releases/download/v0.22.7/app.zip",
            }],
        }
        manager = UpdateManager()
        manager.sources = [source]
        manager.download_mgr = StubDownloadManager()
        with patch("backend.managers.mgr_update.__version__", "0.22.6"), \
             patch("backend.utils.tools.platform.system", return_value="Windows"), \
             patch.object(source.github_mgr, "fetch_release", return_value=release), \
             patch.object(settings.config, "github_mirror_id", "custom:custom"), \
             patch.object(settings.config, "github_mirrors", [{"id": "custom", "url_template": "https://mirror.example/{url}", "support": {"release_asset": {"status": "supported"}}}]):
            info = manager.check_all()
            manager.perform_update_download()

        task = manager.download_mgr.tasks[0]
        self.assertEqual(task["url"], info.download_url)
        self.assertEqual(task["fallback_urls"], info.fallback_urls)
        with patch("backend.managers.mgr_update.GithubManager.record_mirror_result") as record_result:
            task["on_source_result"](info.download_url, False)
        record_result.assert_called_once_with(info.download_url, success=False, capability="release_asset")

    def test_gitee_update_does_not_record_github_mirror_health(self):
        manager = UpdateManager()
        manager.download_mgr = StubDownloadManager()
        info = UpdateInfo(True, "0.22.7", "Gitee update", "https://gitee.com/app.zip", "Gitee")
        manager._start_update_download(info, [])

        callback = manager.download_mgr.tasks[0]["on_source_result"]
        self.assertIsNone(callback)


class TestAdditionalUpdateSources(unittest.TestCase):
    def test_sourceforge_source_parses_latest_download_redirect(self):
        response = Mock()
        response.headers = {"Content-Length": "74733041", "Content-Disposition": 'attachment; filename="RimCrow-v0.24.12-windows.zip"'}
        response.url = "https://twds.dl.sourceforge.net/project/rimcrow/v0.24.12/RimCrow-v0.24.12-windows.zip"
        response.history = []
        with patch("backend.managers.mgr_update.__version__", "0.24.11"), patch("backend.managers.mgr_update.build_retry_session") as build:
            session = build.return_value.__enter__.return_value
            session.get.return_value = response
            info = SourceForgeSource("https://sourceforge.net/projects/rimcrow").check()

        session.get.assert_called_once()
        self.assertEqual(session.get.call_args.args[0], "https://sourceforge.net/projects/rimcrow/files/latest/download?format=json")
        self.assertEqual(info.version, "0.24.12")
        self.assertEqual(info.download_url, "https://sourceforge.net/projects/rimcrow/files/latest/download?format=json")
        self.assertEqual(info.file_size, "71.3 MB")
        response.close.assert_called_once()

    def test_gitee_source_treats_missing_release_as_no_update(self):
        response = Mock(status_code=404)
        with patch("backend.managers.mgr_update.build_retry_session") as build:
            build.return_value.__enter__.return_value.get.return_value = response
            info = GiteeSource("InkyXFeather/RimCrow").check()
        self.assertIsNone(info)

    def test_gitee_source_accepts_download_url_and_sha256_fields(self):
        source = GiteeSource("InkyXFeather/RimCrow")
        response = Mock(status_code=200)
        response.json.return_value = {
            "tag_name": "v0.24.13",
            "draft": False,
            "prerelease": False,
            "assets": [{
                "name": "RimCrow-v0.24.13-windows.zip",
                "download_url": "https://gitee.com/InkyXFeather/RimCrow/releases/download/v0.24.13/app.zip",
                "size": 123,
                "sha256": "a" * 64,
            }],
        }
        response.raise_for_status.return_value = None
        with patch("backend.managers.mgr_update.__version__", "0.24.12"), \
            patch("backend.managers.mgr_update.build_retry_session") as build, \
            patch("backend.utils.tools.platform.system", return_value="Windows"):
            build.return_value.__enter__.return_value.get.return_value = response
            info = source.check()

        self.assertIsNotNone(info)
        self.assertEqual(info.download_url, "https://gitee.com/InkyXFeather/RimCrow/releases/download/v0.24.13/app.zip")
        self.assertEqual(info.hash_algorithm, "sha256")
        self.assertEqual(info.file_hash, "a" * 64)

    def test_configured_update_sources_include_sourceforge_and_gitee(self):
        sources = _build_configured_update_sources({
            "project": {"github_repo": "Inky-Feather/RimCrow"},
            "update": {"sources": [
                {"type": "sourceforge", "url": "https://sourceforge.net/projects/rimcrow"},
                {"type": "gitee", "repo": "InkyXFeather/RimCrow"},
            ]},
        })
        self.assertIsInstance(sources[1], SourceForgeSource)
        self.assertIsInstance(sources[2], GiteeSource)

    def test_check_ignores_same_version_release(self):
        source = GithubSource("Inky-Feather/RimCrow")
        releases = [
            {
                "tag_name": "v0.22.6",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "RimModManager-v0.22.6-windows.zip",
                        "browser_download_url": "https://example.invalid/app.zip",
                    }
                ],
            }
        ]

        with patch("backend.managers.mgr_update.__version__", "0.22.6"), patch.object(source.github_mgr, "fetch_release", return_value=releases[0]):
            info = source.check()

        self.assertIsNone(info)

    def test_check_ignores_prerelease_by_default(self):
        source = GithubSource("Inky-Feather/RimCrow")
        releases = [
            {
                "tag_name": "v0.22.7",
                "draft": False,
                "prerelease": True,
                "assets": [
                    {
                        "name": "RimModManager-v0.22.7-windows.zip",
                        "browser_download_url": "https://example.invalid/app.zip",
                    }
                ],
            }
        ]

        with patch("backend.managers.mgr_update.__version__", "0.22.6"), patch.object(source.github_mgr, "fetch_release", return_value=releases[0]):
            info = source.check()

        self.assertIsNone(info)

    def test_select_asset_prefers_current_system(self):
        source = GithubSource("Inky-Feather/RimCrow")
        assets = [
            {"name": "RimModManager-v0.22.7-windows.zip", "browser_download_url": "https://example.invalid/windows.zip"},
            {"name": "RimCrow-v0.22.7-linux.zip", "browser_download_url": "https://example.invalid/linux.zip"},
            {"name": "RimCrow-v0.22.7.zip", "browser_download_url": "https://example.invalid/generic.zip"},
        ]

        with patch("backend.utils.tools.platform.system", return_value="Linux"):
            asset = source._select_asset(assets)

        self.assertIsNotNone(asset)
        self.assertEqual(asset["browser_download_url"], "https://example.invalid/linux.zip")


class TestLocalUpdateSource(unittest.TestCase):
    def test_check_skips_cache_metadata_without_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            update_dir = Path(temp_dir)
            package_path = update_dir / "update.zip"
            package_path.write_text("zip", encoding="utf-8")
            (update_dir / "update.json").write_text(
                '{"local_file_path": "%s", "changelog": "cached"}' % str(package_path).replace("\\", "\\\\"),
                encoding="utf-8",
            )

            with patch("backend.managers.mgr_update.UPDATE_DIR", str(update_dir)), \
                 patch("backend.managers.mgr_update.logger.warning") as log_warning:
                info = LocalSource().check()

        self.assertIsNone(info)
        self.assertTrue(any("缺少版本号" in str(call.args[0]) for call in log_warning.call_args_list))


class TestLanzouUpdatePackageNames(unittest.TestCase):
    def _file(self, name, file_id):
        return {"id": file_id, "name_all": name, "size": "64 MB", "time": "2026-06-26"}

    def test_select_latest_accepts_old_name_without_platform(self):
        parser = LanzouParser()
        files = [
            parser._build_file_info(self._file("RimModManager_v0.22.7.zip", "old")),
            parser._build_file_info(self._file("readme.txt", "txt")),
        ]

        with patch("backend.utils.tools.platform.system", return_value="Windows"):
            latest = parser._select_latest_update_file(files)

        self.assertIsNotNone(latest)
        self.assertEqual(latest["id"], "old")
        self.assertEqual(latest["version"], "0.22.7")
        self.assertFalse(latest["platform_matched"])

    def test_select_latest_prefers_current_system_for_new_name(self):
        parser = LanzouParser()

        with patch("backend.utils.tools.platform.system", return_value="Windows"):
            files = [
                parser._build_file_info(self._file("RimCrow-v0.22.8-linux.zip", "linux")),
                parser._build_file_info(self._file("RimCrow-v0.22.7-windows.zip", "windows")),
                parser._build_file_info(self._file("RimModManager_v0.22.6.zip", "old")),
            ]
            latest = parser._select_latest_update_file(files)

        self.assertIsNotNone(latest)
        self.assertEqual(latest["id"], "windows")
        self.assertEqual(latest["version"], "0.22.7")
        self.assertTrue(latest["platform_matched"])

    def test_select_latest_supports_new_name_without_v_prefix(self):
        parser = LanzouParser()

        with patch("backend.utils.tools.platform.system", return_value="Windows"):
            info = parser._build_file_info(self._file("RimCrow-0.22.8-win64.zip", "win64"))

        self.assertTrue(info["is_update_package"])
        self.assertTrue(info["platform_compatible"])
        self.assertEqual(info["version"], "0.22.8")

    def test_select_latest_ignores_unknown_zip_with_version(self):
        parser = LanzouParser()

        with patch("backend.utils.tools.platform.system", return_value="Windows"):
            files = [
                parser._build_file_info(self._file("OtherTool-v9.9.9-windows.zip", "other")),
                parser._build_file_info(self._file("RimCrow-v0.22.8-windows.zip", "rimcrow")),
            ]
            latest = parser._select_latest_update_file(files)

        self.assertIsNotNone(latest)
        self.assertEqual(latest["id"], "rimcrow")
        self.assertEqual(latest["version"], "0.22.8")

    def test_get_file_retries_acw_challenge_and_returns_https_url(self):
        class FakeResponse:
            close = Mock()

            def __init__(self, text="", payload=None, status_code=200, headers=None):
                self.text = text
                self._payload = payload or {}
                self.status_code = status_code
                self.headers = headers or {}

            def json(self):
                return self._payload

        class FakeCookies:
            def __init__(self):
                self.values = {}

            def set(self, name, value, **kwargs):
                self.values[name] = value

        class FakeSession:
            def __init__(self):
                self.cookies = FakeCookies()
                self.get_calls = []

            def get(self, url, **kwargs):
                self.get_calls.append(url)
                if url == "https://wwbns.lanzouu.com/iabc":
                    if len([item for item in self.get_calls if item == url]) == 1:
                        return FakeResponse("<html><script>var arg1='B0F7DFAACD80635E8AF18DF2B24B461666A78C0A';document.cookie='acw_sc__v2=';</script></html>")
                    return FakeResponse('<span class="p7">文件描述：</span><br>更新说明</td><iframe src="/fn?abc"></iframe><script>var fid = 123;</script>')
                if url == "https://wwbns.lanzouu.com/fn?abc":
                    return FakeResponse("var wp_sign = 'sign'; var ajaxdata = 'key'; var kdns =1; $.ajax({url : '/ajaxm.php?file=123', data : { 'action':'downprocess','websignkey':ajaxdata,'signs':ajaxdata,'sign':wp_sign,'websign':'','kd':kdns,'ves':1 }})")
                if url == "https://zip.example.com/file/path.zip":
                    return FakeResponse(status_code=200)
                raise AssertionError(url)

            def post(self, url, **kwargs):
                self.post_url = url
                return FakeResponse(payload={"zt": 1, "dom": "zip.example.com", "url": "path.zip"})

        parser = LanzouParser()
        parser.session = FakeSession()

        info = parser.get_file("https://wwbns.lanzouu.com/iabc")

        self.assertEqual(parser.session.cookies.values["acw_sc__v2"], "6a4279dcdc74c4d9b0ed0b4908616fa7aa7b6b72")
        self.assertEqual(info["download_url"], "https://zip.example.com/file/path.zip")

    def test_get_file_uses_declared_iframe_ajax_endpoint(self):
        class FakeResponse:
            status_code = 200
            headers = {}
            close = Mock()

            def __init__(self, text="", payload=None):
                self.text = text
                self.payload = payload or {}

            def json(self):
                return self.payload

        class FakeSession:
            def __init__(self):
                self.cookies = Mock()
                self.post_url = ""

            def get(self, url, **kwargs):
                if url.endswith("/item"):
                    return FakeResponse('<span class="p7">文件描述：</span><br>动态接口</td><script>var fid = 321;</script><iframe src="/download/frame?id=1"></iframe>')
                if url.endswith("/download/frame?id=1"):
                    return FakeResponse("var sign = 's'; var key = 'k'; $.ajax({url: 'https://apifile.woozooo.com/ajaxfile.php', data: {action: 'downprocess', sign: sign, websignkey: key, kd: 1}})")
                if url == "https://zip.example.com/file/path.zip":
                    return FakeResponse()
                raise AssertionError(url)

            def post(self, url, **kwargs):
                self.post_url = url
                self.payload = kwargs["data"]
                return FakeResponse(payload={"zt": 1, "dom": "zip.example.com", "url": "path.zip"})

        parser = LanzouParser()
        parser.session = FakeSession()
        info = parser.get_file("https://wwbns.lanzouu.com/item")

        self.assertEqual(parser.session.post_url, "https://apifile.woozooo.com/ajaxfile.php")
        self.assertEqual(parser.session.payload["websignkey"], "k")
        self.assertEqual(info["download_url"], "https://zip.example.com/file/path.zip")


class TestUpdateManagerSources(unittest.TestCase):
    def _manager_with_sources(self, infos):
        manager = UpdateManager()
        manager.sources = [StubUpdateSource(info) for info in infos]
        manager.download_mgr = StubDownloadManager()
        manager.current_update_info = None
        manager.active_download_task_id = None
        manager.active_download_version = None
        manager.download_contexts = {}
        return manager

    def test_check_all_keeps_same_latest_version_sources_in_priority_order(self):
        lanzou = UpdateInfo(True, "0.22.7", "蓝奏云更新", "https://example.invalid/lanzou.zip", "蓝奏云", file_size="64 MB")
        github = UpdateInfo(True, "0.22.7", "## GitHub 更新", "https://example.invalid/github.zip", "GitHub", file_size="64.1 MB")
        manager = self._manager_with_sources([lanzou, github])

        with patch("backend.managers.mgr_update.__version__", "0.22.6"), patch.object(manager, "_find_cached_file", return_value=None):
            info = manager.check_all()

        self.assertEqual(info.source_name, "蓝奏云")
        self.assertEqual(info.version, "0.22.7")
        self.assertEqual([source["source_name"] for source in info.sources], ["蓝奏云", "GitHub"])

    def test_check_all_only_keeps_highest_version_sources(self):
        lanzou = UpdateInfo(True, "0.22.7", "蓝奏云更新", "https://example.invalid/lanzou.zip", "蓝奏云")
        github = UpdateInfo(True, "0.22.8", "## GitHub 更新", "https://example.invalid/github.zip", "GitHub")
        manager = self._manager_with_sources([lanzou, github])

        with patch("backend.managers.mgr_update.__version__", "0.22.6"), patch.object(manager, "_find_cached_file", return_value=None):
            info = manager.check_all()

        self.assertEqual(info.source_name, "GitHub")
        self.assertEqual(info.version, "0.22.8")
        self.assertEqual([source["source_name"] for source in info.sources], ["GitHub"])

    def test_download_error_tries_next_same_version_source(self):
        manager = self._manager_with_sources([])
        manager.current_update_info = UpdateInfo(
            True,
            "0.22.7",
            "蓝奏云更新",
            "https://example.invalid/lanzou.zip",
            "蓝奏云",
            sources=[
                UpdateInfo(True, "0.22.7", "蓝奏云更新", "https://example.invalid/lanzou.zip", "蓝奏云").to_source_dict(),
                UpdateInfo(True, "0.22.7", "## GitHub 更新", "https://example.invalid/github.zip", "GitHub", file_hash="abc", hash_algorithm="sha256").to_source_dict(),
            ],
        )
        first = manager.perform_update_download()
        failed_task = DownloadTask(url="https://example.invalid/lanzou.zip", dest_path="")
        failed_task.task_id = first["task_id"]
        failed_task.error_msg = "网络错误"
        failed_task.metadata = manager.download_mgr.tasks[0]["metadata"]

        with patch("backend.managers.mgr_update.EventBus.emit_progress"):
            manager._on_download_error(failed_task)

        self.assertEqual(len(manager.download_mgr.tasks), 2)
        self.assertTrue(manager.download_mgr.tasks[0]["metadata"]["has_fallback_source"])
        self.assertEqual(manager.current_update_info.source_name, "GitHub")
        self.assertEqual(manager.download_mgr.tasks[1]["url"], "https://example.invalid/github.zip")
        self.assertEqual(manager.download_mgr.tasks[1]["expected_hash"], "abc")
        self.assertEqual(manager.download_mgr.tasks[1]["hash_algorithm"], "sha256")
        self.assertFalse(manager.download_mgr.tasks[1]["metadata"]["has_fallback_source"])

    def test_check_all_raises_when_all_remote_sources_fail(self):
        manager = self._manager_with_sources([])
        manager.sources = [FailingUpdateSource(), FailingUpdateSource()]

        with patch("backend.managers.mgr_update.__version__", "0.22.6"):
            with self.assertRaises(UpdateSourceError):
                manager.check_all()

    def test_check_all_marks_partial_when_no_update_but_one_source_failed(self):
        manager = self._manager_with_sources([])
        manager.sources = [StubUpdateSource(None), FailingUpdateSource()]

        with patch("backend.managers.mgr_update.__version__", "0.22.6"):
            info = manager.check_all()

        self.assertFalse(info.has_update)
        self.assertEqual(info.check_status, "partial")
        self.assertEqual([result["status"] for result in info.source_results], ["no_update", "failed"])

    def test_check_all_uses_shared_error_for_wrapped_source_timeout(self):
        manager = self._manager_with_sources([None])
        source = Mock()
        manager.sources.append(source)
        try:
            raise UpdateSourceError("更新清单请求失败") from requests.Timeout("slow")
        except UpdateSourceError as exc:
            source.check.side_effect = exc
        info = manager.check_all()
        result = info.source_results[1]
        self.assertEqual(result["error_code"], "NETWORK.TIMEOUT")
        self.assertEqual(result["message_key"], "errors.network.timeout")
        self.assertIn("超时", result["error"])

    def test_repeated_download_returns_active_task(self):
        manager = self._manager_with_sources([])
        manager.current_update_info = UpdateInfo(True, "0.22.7", "更新", "https://example.invalid/lanzou.zip", "蓝奏云")

        first = manager.perform_update_download()
        second = manager.perform_update_download()
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(len(manager.download_mgr.tasks), 1)

    def test_download_complete_uses_task_snapshot(self):
        manager = self._manager_with_sources([])
        original = UpdateInfo(True, "0.22.7", "蓝奏云更新", "https://example.invalid/lanzou.zip", "蓝奏云")
        manager.current_update_info = original
        started = manager.perform_update_download()
        manager.current_update_info = UpdateInfo(True, "0.22.8", "GitHub 更新", "https://example.invalid/github.zip", "GitHub")

        task = DownloadTask(url="https://example.invalid/lanzou.zip", dest_path="F:/tmp/update_v0.22.7.zip")
        task.task_id = started["task_id"]

        with patch("backend.managers.mgr_update.os.path.exists", return_value=True), \
            patch.object(manager, "_save_metadata_file"), \
            patch.object(manager, "_clean_old_cache"), \
            patch("backend.managers.mgr_update.EventBus.emit_progress"):
            manager._on_download_complete(task)

        self.assertEqual(manager.current_update_info.version, "0.22.7")
        self.assertEqual(manager.current_update_info.source_name, "蓝奏云")
        self.assertEqual(manager.current_update_info.local_status, "ready")

    def test_hot_swap_from_rimmodmanager_launches_rimcrow_exe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_root = root / "app"
            install_root.mkdir()
            current_exe = install_root / "RimModManager.exe"
            current_exe.write_text("old", encoding="utf-8")
            zip_path = root / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("RimCrow/RimCrow.exe", "new")

            manager = self._manager_with_sources([])
            with patch("backend.managers.mgr_update.sys.executable", str(current_exe)), \
                patch("backend.managers.mgr_update.backup_config_for_update"), \
                patch("backend.managers.mgr_update.launch_new_application") as launch_mock, \
                patch("backend.managers.mgr_update.os._exit", side_effect=SystemExit):
                with self.assertRaises(SystemExit):
                    manager.execute_hot_swap(str(zip_path))

            new_exe = install_root / "RimCrow.exe"
            self.assertTrue(new_exe.exists())
            self.assertTrue((install_root / "RimModManager.exe.old").exists())
            launch_mock.assert_called_once_with(str(new_exe))


class TestConfiguredUpdateSources(unittest.TestCase):
    def test_build_configured_update_sources_reads_project_meta(self):
        sources = _build_configured_update_sources({
            "project": {"github_repo": "Inky-Feather/RimCrow"},
            "update": {
                "sources": [
                    {"type": "lanzou", "url": "https://example.invalid/lanzou", "password": "abcd"},
                    {"type": "github"},
                ]
            },
        })

        self.assertEqual(len(sources), 3)
        self.assertIsInstance(sources[0], LocalSource)
        self.assertIsInstance(sources[1], LanzouSource)
        self.assertEqual(sources[1].url, "https://example.invalid/lanzou")
        self.assertEqual(sources[1].pwd, "abcd")
        self.assertIsInstance(sources[2], GithubSource)
        self.assertEqual(sources[2].repo, "Inky-Feather/RimCrow")

    def test_build_configured_update_sources_falls_back_when_meta_missing(self):
        sources = _build_configured_update_sources({})

        self.assertEqual(len(sources), 3)
        self.assertIsInstance(sources[0], LocalSource)
        self.assertIsInstance(sources[1], LanzouSource)
        self.assertIsInstance(sources[2], GithubSource)

    def test_manifest_source_expands_same_file_mirrors(self):
        payload = {
            "schema_version": 1,
            "product": "RimCrow",
            "version": "99.0.0",
            "changelog": "清单更新",
            "assets": [{
                "platform": "windows-x64",
                "size": 2048,
                "sha256": "a" * 64,
                "mirrors": [
                    {"id": "r2", "label": "R2", "url": "https://r2.example/update.zip"},
                    {"id": "sourceforge", "label": "SourceForge", "url": "https://sf.example/update.zip"},
                ],
            }],
        }
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = payload
        with patch("backend.managers.mgr_update.build_retry_session") as build:
            build.return_value.__enter__.return_value.get.return_value = response
            info = ManifestSource("https://example.invalid/stable.json", "r2", "R2 清单").check()

        self.assertIsNotNone(info)
        self.assertEqual(info.source_name, "R2")
        self.assertEqual(info.file_hash, "a" * 64)
        self.assertEqual([item["source_name"] for item in info.sources], ["R2", "SourceForge"])

    def test_manifest_source_rejects_missing_download_mirrors(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"product": "RimCrow", "version": "99.0.0", "assets": [{"platform": "windows-x64", "mirrors": []}]}
        with patch("backend.managers.mgr_update.build_retry_session") as build:
            build.return_value.__enter__.return_value.get.return_value = response
            with self.assertRaises(UpdateSourceError):
                ManifestSource("https://example.invalid/stable.json").check()

    def test_manifest_source_requires_schema_and_sha256(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "product": "RimCrow", "version": "99.0.0",
            "assets": [{"platform": "windows-x64", "mirrors": [{"url": "https://example.invalid/update.zip"}]}],
        }
        with patch("backend.managers.mgr_update.build_retry_session") as build:
            build.return_value.__enter__.return_value.get.return_value = response
            with self.assertRaisesRegex(UpdateSourceError, "schema_version"):
                ManifestSource("https://example.invalid/stable.json").check()

    def test_manifest_source_reports_html_response(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.side_effect = ValueError("not json")
        with patch("backend.managers.mgr_update.build_retry_session") as build:
            build.return_value.__enter__.return_value.get.return_value = response
            with self.assertRaisesRegex(UpdateSourceError, "HTML"):
                ManifestSource("https://example.invalid/stable.json").check()

    def test_build_configured_update_sources_accepts_manifest(self):
        sources = _build_configured_update_sources({
            "update": {"sources": [{"type": "manifest", "id": "r2", "label": "R2", "url": "https://example.invalid/stable.json"}]}
        })

        self.assertEqual(len(sources), 2)
        self.assertIsInstance(sources[0], LocalSource)
        self.assertIsInstance(sources[1], ManifestSource)
        self.assertEqual(sources[1].source_id, "r2")


if __name__ == "__main__":
    unittest.main()
