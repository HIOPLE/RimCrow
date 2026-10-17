import unittest
from unittest.mock import MagicMock, patch

from backend.managers.mgr_github import GithubManager
from backend.settings import settings


class TestGithubMirrorCatalog(unittest.TestCase):
    def setUp(self):
        self.manager = GithubManager()

    def test_refresh_filters_remote_failures_before_local_probe(self):
        groups = {
            "ghproxy_next": ([
                {"id": "good", "label": "Good", "url_template": "https://good.example/{url}", "url_mode": "full_url", "sources": ["ghproxy_next"], "remote_status": "unknown"},
            ], None),
            "hiwiki": ([
                {"id": "ok", "label": "OK", "url_template": "https://ok.example/{url}", "url_mode": "full_url", "sources": ["hiwiki"], "remote_status": "ok", "remote_latency_ms": 10},
            ], None),
        }

        def probe(template, target, url_mode="full_url"):
            return template, {"status": "supported", "http_status": 200}

        with patch.object(GithubManager, "_fetch_mirror_source", side_effect=lambda source: groups[source]), \
             patch.object(GithubManager, "_probe_mirror_url", side_effect=probe):
            payload = GithubManager.refresh_github_mirror_catalog(probe_limit=2)

        self.assertEqual(payload["status"], "ready")
        self.assertEqual({item["label"] for item in payload["mirrors"]}, {"Good", "OK"})
        self.assertTrue(all(item["health_status"] == "success" for item in payload["mirrors"]))

    def test_auto_mode_returns_multiple_dynamic_candidates_then_direct(self):
        old_id = settings.config.github_mirror_id
        old_dynamic_enabled = settings.config.github_dynamic_mirrors_enabled
        old_catalog = GithubManager._mirror_catalog
        self.addCleanup(setattr, settings.config, "github_mirror_id", old_id)
        self.addCleanup(setattr, settings.config, "github_dynamic_mirrors_enabled", old_dynamic_enabled)
        self.addCleanup(setattr, GithubManager, "_mirror_catalog", old_catalog)
        settings.config.github_mirror_id = "auto"
        settings.config.github_dynamic_mirrors_enabled = True
        GithubManager._mirror_catalog = {
            "status": "ready",
            "mirrors": [
                {"url_template": "https://fast.example/{url}", "url_mode": "full_url", "health_status": "success", "remote_latency_ms": 10, "support": {"archive": {"status": "supported"}}, "health": {"archive": {"status": "valid", "latency_ms": 10}}},
                {"url_template": "https://slow.example/{url}", "url_mode": "full_url", "health_status": "success", "remote_latency_ms": 20, "support": {"archive": {"status": "supported"}}, "health": {"archive": {"status": "valid", "latency_ms": 20}}},
            ],
        }
        self.assertEqual(
            GithubManager._github_download_candidates("https://github.com/a/b/archive/main.zip", "archive"),
            [
                "https://fast.example/https://github.com/a/b/archive/main.zip",
                "https://slow.example/https://github.com/a/b/archive/main.zip",
                "https://gh-proxy.org/https://github.com/a/b/archive/main.zip",
                "https://github.com/a/b/archive/main.zip",
            ],
        )

    def test_hiwiki_unknown_status_is_kept_for_local_probe(self):
        nodes = GithubManager._parse_hiwiki_nodes({"mirrors": [{
            "label": "Pending", "base": "https://pending.example", "pathPrefix": "https://github.com",
            "status": "pending", "latency": 20,
        }]})
        self.assertEqual(nodes[0]["remote_status"], "unknown")


class TestGithubMirrorPool(unittest.TestCase):
    def setUp(self):
        self.old_id = settings.config.github_mirror_id
        self.old_dynamic_enabled = settings.config.github_dynamic_mirrors_enabled
        self.old_mirrors = settings.config.github_mirrors
        self.old_catalog = GithubManager._mirror_catalog
        self.old_loaded = GithubManager._mirror_catalog_loaded
        settings.config.github_mirrors = [{
            "id": "custom", "label": "Custom", "url_template": "https://custom.example/{url}",
            "support": {"web": {"status": "supported"}},
            "health": {"web": {"status": "valid", "latency_ms": 80}},
        }]
        GithubManager._mirror_catalog_loaded = True
        GithubManager._mirror_catalog = {"status": "ready", "mirrors": [{
            "id": "dynamic", "label": "Dynamic", "url_template": "https://dynamic.example/{url}",
            "origin": "dynamic", "support": {"web": {"status": "supported"}},
            "health": {"web": {"status": "valid", "latency_ms": 10}},
        }]}
        self.addCleanup(self.restore)

    def restore(self):
        settings.config.github_mirror_id = self.old_id
        settings.config.github_dynamic_mirrors_enabled = self.old_dynamic_enabled
        settings.config.github_mirrors = self.old_mirrors
        GithubManager._mirror_catalog = self.old_catalog
        GithubManager._mirror_catalog_loaded = self.old_loaded

    def test_selection_returns_one_node_or_auto_pool(self):
        settings.config.github_dynamic_mirrors_enabled = True
        self.assertEqual([n["origin"] for n in GithubManager.get_effective_mirrors("custom:custom", "web")], ["custom"])
        self.assertEqual([n["origin"] for n in GithubManager.get_effective_mirrors("dynamic:dynamic", "web")], ["dynamic"])
        self.assertEqual([n["origin"] for n in GithubManager.get_effective_mirrors("auto", "web")], ["dynamic", "custom", "builtin"])

    def test_auto_uses_cached_health_and_never_refreshes(self):
        with patch.object(GithubManager, "start_mirror_catalog_refresh") as refresh:
            self.assertEqual(GithubManager._github_download_candidates("https://github.com/a/b", "web")[0], "https://github.com/a/b")
            refresh.assert_not_called()
        settings.config.github_mirror_id = "auto"
        settings.config.github_dynamic_mirrors_enabled = True
        candidates = GithubManager._github_download_candidates("https://github.com/a/b", "web")
        self.assertEqual(candidates[:2], [
            "https://dynamic.example/https://github.com/a/b",
            "https://custom.example/https://github.com/a/b",
        ])


class TestGithubMirrorProbe(unittest.TestCase):
    def setUp(self):
        self.manager = GithubManager()
        GithubManager._response_cache.clear()

    def test_probe_records_each_capability_status(self):
        def probe(_template, target, _mode="full_url", **_kwargs):
            capability = self.manager.github_url_capability(target)
            return target, {"status": {"web": "supported", "raw": "unsupported"}.get(capability, "unknown"), "http_status": 200}

        with patch.object(GithubManager, "_probe_mirror_url", side_effect=probe):
            result = self.manager.probe_github_mirror({"url_template": "https://mirror.example/{url}"})

        self.assertEqual(result["support"]["web"]["status"], "supported")
        self.assertEqual(result["support"]["raw"]["status"], "unsupported")
        self.assertEqual(result["support"]["archive"]["status"], "unknown")
        self.assertEqual(result["support"]["release_asset"]["status"], "unknown")

    def test_unsupported_capability_uses_direct_only(self):
        with patch.object(settings.config, "github_mirror_id", "custom:custom"), patch.object(
            settings.config,
            "github_mirrors",
            [{"id": "custom", "url_template": "https://mirror.example/{url}", "support": {"web": {"status": "unsupported"}}}],
        ):
            self.assertEqual(
                self.manager._github_download_candidates("https://github.com/team/mod", "web"),
                ["https://github.com/team/mod"],
            )

    def test_repo_page_falls_back_after_mirror_404(self):
        mirror_response = MagicMock(status_code=404, text="")
        direct_response = MagicMock(status_code=200, text="<html>ok</html>")
        session = MagicMock()
        session.get.side_effect = [mirror_response, direct_response]
        manager = MagicMock()
        manager.__enter__.return_value = session
        manager.__exit__.return_value = None

        with patch.object(settings.config, "github_mirror_id", "custom:custom"), patch.object(
            settings.config,
            "github_mirrors",
            [{"id": "custom", "url_template": "https://mirror.example/{url}", "support": {"web": {"status": "supported"}}}],
        ), patch("backend.managers.mgr_github.build_retry_session", return_value=manager):
            payload = self.manager.fetch_repo_page("team", "mod", missing_ok=True)

        self.assertEqual(payload, "<html>ok</html>")
        self.assertEqual([call.args[0] for call in session.get.call_args_list], [
            "https://mirror.example/https://github.com/team/mod",
            "https://github.com/team/mod",
        ])


if __name__ == "__main__":
    unittest.main()
