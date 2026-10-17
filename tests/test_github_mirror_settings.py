import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.settings import AppConfig, SettingsManager, normalize_github_mirrors


class TestGithubMirrorSettings(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.mirror_path = Path(directory.name) / "github_mirrors.json"
        self.config_path = Path(directory.name) / "config.json"
        for name, value in (("GITHUB_MIRRORS_PATH", self.mirror_path), ("CONFIG_PATH", self.config_path)):
            patcher = patch(f"backend.settings.{name}", value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manager = object.__new__(SettingsManager)
        self.manager.config = AppConfig()
        self.manager._save_lock = threading.Lock()
        self.manager._sync_derived_paths = lambda: None
        self.manager.get_secret_status = lambda: {}
        self.custom = {"id": "custom", "label": "自定义", "url_template": "https://example.org/{url}", "support": {"raw": {"status": "supported"}}}

    def test_hosts_sources_defaults_and_legacy_migration(self):
        defaults = AppConfig().network.hosts_sources
        self.assertEqual([row["url"] for row in defaults], [
            "https://raw.hellogithub.com/hosts",
            "https://cdn.jsdelivr.net/gh/Clov614/SteamHostSync@main/Hosts_steam",
        ])
        self.assertTrue(all(row["enabled"] for row in defaults))
        for legacy, expected in (
            ("https://raw.hellogithub.com/hosts", defaults),
            ("https://custom.example/hosts\nhttps://second.example/hosts.json", [
                {"url": "https://custom.example/hosts", "enabled": True},
                {"url": "https://second.example/hosts.json", "enabled": True},
            ]),
        ):
            with self.subTest(legacy=legacy):
                fragment = self.manager._parse_config_fragment({"network": {"github_hosts_url": legacy}})
                self.manager._apply_config_fragment(fragment)
                self.assertEqual(self.manager.config.network.hosts_sources, expected)
                self.assertNotIn("github_hosts_url", self.manager.to_storage_dict()["network"])

    def test_empty_or_disabled_hosts_sources_survive_config_roundtrip(self):
        for sources in ([], [{"url": " https://custom.example/hosts ", "enabled": False}]):
            with self.subTest(sources=sources):
                fragment = self.manager._parse_config_fragment({"network": {"hosts_sources": sources, "github_hosts_url": "https://old.example/hosts"}})
                self.manager._apply_config_fragment(fragment)
                stored = self.manager.to_storage_dict()
                self.manager._apply_config_fragment(self.manager._parse_config_fragment(stored))
                self.assertEqual(self.manager.config.network.hosts_sources, [
                    {**row, "url": row["url"].strip()} for row in sources
                ])

    def test_legacy_migration_and_edit_persist_in_separate_file(self):
        self.manager.config.github_mirrors = [self.custom]
        self.manager.config.github_mirror_id = "custom:custom"
        self.manager._load_github_mirrors()
        self.assertEqual(json.loads(self.mirror_path.read_text("utf-8"))["mirrors"][0]["support"], self.custom["support"])
        self.manager.update_from_dict({"github_mirrors": [{**self.custom, "label": "新名称"}]})
        stored = json.loads(self.config_path.read_text("utf-8"))
        self.assertNotIn("github_mirrors", stored)
        self.assertEqual(stored["github_mirror_id"], "custom:custom")
        self.assertEqual(json.loads(self.mirror_path.read_text("utf-8"))["mirrors"][0]["label"], "新名称")
        public = self.manager.to_public_dict()
        self.assertEqual(public["_github_mirrors_path"], str(self.mirror_path))
        self.assertEqual(public["github_mirrors"][0]["label"], "新名称")

    def test_independent_file_wins_and_preserves_selection_on_startup(self):
        self.mirror_path.write_text(json.dumps({"mirrors": [self.custom]}), encoding="utf-8")
        self.manager.config.github_mirror_id = "custom:custom"
        self.manager._normalize_config()
        self.manager._load_github_mirrors()
        self.assertEqual(self.manager.config.github_mirror_id, "custom:custom")
        self.assertEqual(self.manager.config.github_mirrors[0]["support"], self.custom["support"])
        self.manager.update_from_dict({"github_mirrors": []})
        self.assertEqual(self.manager.config.github_mirror_id, "direct")
        self.manager._load_github_mirrors()
        self.assertEqual(self.manager.config.github_mirrors, [])

    def test_invalid_file_and_invalid_edits_do_not_destroy_saved_data(self):
        self.mirror_path.write_text("{broken", encoding="utf-8")
        self.manager._load_github_mirrors()
        self.manager.save()
        self.assertEqual(self.mirror_path.read_text("utf-8"), "{broken")
        self.assertIn("github_mirrors", self.manager.to_storage_dict())
        original = list(self.manager.config.github_mirrors)
        for override in (
            {"id": "direct"}, {"url_template": "http://example.org/{url}"},
            {"url_template": "https://example.org/{url}/{url}"}, {"support": {"unknown-capability": {"status": "supported"}}},
            {"url_template": "https://user@example.org/{url}"},
            {"url_template": "https://example.org:99999/{url}"},
            {"url_template": "https://{url}.example.org/"},
        ):
            with self.subTest(override=override), self.assertRaises((ValueError, TypeError)):
                self.manager.update_from_dict({"github_mirrors": [{**self.custom, **override}]})
            self.assertEqual(self.manager.config.github_mirrors, original)
            self.assertEqual(self.mirror_path.read_text("utf-8"), "{broken")
        with self.assertRaises(ValueError):
            normalize_github_mirrors([self.custom, self.custom], strict=True)

    def test_extended_capabilities_are_valid(self):
        mirror = {**self.custom, "support": {key: {"status": "supported"} for key in ("api", "git_clone", "gist", "lfs_media")}, "url_mode": "github_path"}
        self.assertEqual(set(normalize_github_mirrors([mirror], strict=True)[0]["support"]), {"api", "git_clone", "gist", "lfs_media"})

    def test_https_mirror_may_use_valid_nonstandard_port(self):
        mirror = {**self.custom, "url_template": "https://mirror.example:8443/{url}"}
        self.assertEqual(normalize_github_mirrors([mirror], strict=True)[0]["url_template"], mirror["url_template"])

    def test_failed_migration_keeps_legacy_list_in_main_config(self):
        self.manager.config.github_mirrors = [self.custom]
        with patch("backend.settings.write_json_atomic", side_effect=PermissionError("read only")):
            self.manager._load_github_mirrors()
        self.assertEqual(self.manager.to_storage_dict()["github_mirrors"][0]["support"], self.custom["support"])


if __name__ == "__main__":
    unittest.main()
