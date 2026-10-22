import hashlib
from unittest.mock import MagicMock

import pytest

from scripts import sync_gitee as sync


TAG = "v0.24.12"
NAME = f"RimCrow-{TAG}-windows.zip"
DIGEST = hashlib.sha256(b"package").hexdigest()


def asset(name=NAME, **overrides):
    return {"name": name, "size": 7, "state": "uploaded", **overrides}


def test_filter_excludes_test_packages_and_requires_a_package():
    selected = sync.select_assets([
        asset(), asset(f"RimCrow-{TAG}-windows-test.zip"), asset("other.zip"),
        asset("SHA256SUMS.txt"), asset("RimCrow-v0.0.1-linux.zip"),
    ], TAG, sync.DEFAULT_PATTERNS, 100)
    assert [item["name"] for item in selected] == [NAME, "SHA256SUMS.txt"]
    assert sync.select_assets([asset("custom.zip")], TAG, "custom.zip", 100)
    with pytest.raises(ValueError, match="ZIP"):
        sync.select_assets([asset("SHA256SUMS.txt")], TAG, sync.DEFAULT_PATTERNS, 100)


@pytest.mark.parametrize("overrides", [{"size": 101}, {"size": 0}, {"state": "new"},
                                        {"name": "../bad.zip"}, {"name": "bad\\file.zip"}])
def test_filter_rejects_invalid_asset_before_any_write(overrides):
    with pytest.raises(ValueError):
        sync.select_assets([asset(**overrides)], TAG, "*.zip", 100)


def test_duplicate_assets_rejected():
    with pytest.raises(ValueError, match="重复"):
        sync.select_assets([asset(), asset()], TAG, sync.DEFAULT_PATTERNS, 100)


@pytest.mark.parametrize("status,missing", [(404, True), (403, False), (429, False), (500, False)])
def test_only_404_is_treated_as_missing(status, missing):
    session = MagicMock()
    response = session.request.return_value
    response.status_code = status
    if missing:
        assert sync.api_request(session, "GET", sync.GITEE_API, "/releases/tags/test", True) is None
    else:
        with pytest.raises(RuntimeError, match=str(status)):
            sync.api_request(session, "GET", sync.GITEE_API, "/releases/tags/test", True)
    assert session.request.call_args.kwargs["allow_redirects"] is False
    assert session.request.call_args.kwargs["timeout"] == (15, 120)


def test_pagination_reads_more_than_first_page(monkeypatch):
    api = MagicMock(side_effect=[[{"id": i} for i in range(100)], [{"id": 100}]])
    monkeypatch.setattr(sync, "api_request", api)
    assert len(sync.list_pages(MagicMock(), sync.GITEE_API, "/assets")) == 101
    assert api.call_args.kwargs["params"]["page"] == 2


def test_download_hash_streams_without_platform_credentials(tmp_path, monkeypatch):
    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = 200
    response.iter_content.return_value = [b"pack", b"age"]
    get = MagicMock(return_value=response)
    monkeypatch.setattr(sync.requests, "get", get)
    output = tmp_path / "asset.zip"
    assert sync.download_hash("https://example.com/file", output, 100, 7) == DIGEST
    assert output.read_bytes() == b"package"
    assert "headers" not in get.call_args.kwargs
    response.iter_content.return_value = [b"package"]
    with pytest.raises(ValueError, match="大小"):
        sync.download_hash("https://example.com/file", None, 3)
    response.iter_content.return_value = [b"package"]
    with pytest.raises(ValueError, match="不符"):
        sync.download_hash("https://example.com/file", None, 100, 8)


def test_gitee_download_allows_only_known_https_redirect_hosts(monkeypatch):
    first = MagicMock()
    first.__enter__.return_value = first
    first.status_code = 302
    first.headers = {"Location": "https://foruda.gitee.com/file?token=signed"}
    last = MagicMock()
    last.__enter__.return_value = last
    last.status_code = 200
    last.iter_content.return_value = [b"package"]
    get = MagicMock(side_effect=[first, last])
    monkeypatch.setattr(sync.requests, "get", get)
    assert sync.download_hash("https://gitee.com/file", None, 100,
                             allowed_hosts={"gitee.com", "foruda.gitee.com"}) == DIGEST
    assert get.call_args_list[0].kwargs["allow_redirects"] is False
    assert get.call_args_list[1].args[0] == "https://foruda.gitee.com/file?token=signed"
    assert all("headers" not in call.kwargs for call in get.call_args_list)
    first.headers["Location"] = "https://example.com/steal"
    get.side_effect = [first]
    with pytest.raises(ValueError, match="重定向目标"):
        sync.download_hash("https://gitee.com/file", None, 100,
                           allowed_hosts={"gitee.com", "foruda.gitee.com"})


@pytest.mark.parametrize("only_tag", [False, True])
def test_forced_push_is_token_safe_and_does_not_prune(tmp_path, monkeypatch, only_tag):
    run = MagicMock()
    monkeypatch.setattr(sync.subprocess, "run", run)
    sync.sync_code(tmp_path, "secret-test-token", True, TAG, only_tag)
    commands = [call.args[0] for call in run.call_args_list]
    push = commands[-1]
    assert "--force" in push and "--dry-run" in push
    assert "--prune" not in push and "--mirror" not in push
    assert all("secret-test-token" not in " ".join(command) for command in commands)
    assert "secret-test-token" not in (tmp_path / "askpass.sh").read_text()
    assert all(call.kwargs["timeout"] == 300 for call in run.call_args_list)
    for call in run.call_args_list:
        assert ("GITEE_TOKEN" in call.kwargs["env"]) == ("push" in call.args[0])
    if only_tag:
        assert push[-1] == f"refs/tags/{TAG}:refs/tags/{TAG}"
    else:
        assert push[-2:] == ["refs/heads/*:refs/heads/*", "refs/tags/*:refs/tags/*"]


def prepare_main(monkeypatch, tmp_path, *, existing=False, conflict=False, dry_run=True, sync_code=False):
    monkeypatch.setenv("GITEE_TOKEN", "fake-token")
    monkeypatch.setenv("SYNC_CODE", str(sync_code).lower())
    monkeypatch.setenv("SYNC_DRY_RUN", str(dry_run).lower())
    monkeypatch.setenv("SYNC_RELEASE_TAG", TAG)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.delenv("GITEE_ASSET_PATTERNS", raising=False)
    monkeypatch.delenv("GITEE_MAX_ASSET_BYTES", raising=False)
    release = {"id": 1, "tag_name": TAG, "name": "Release", "body": "notes", "draft": False, "prerelease": False}
    source_asset = asset(browser_download_url=f"https://github.com/{sync.SOURCE}/releases/download/{TAG}/{NAME}",
                         digest=f"sha256:{DIGEST}")

    def api(session, method, base, path, **kwargs):
        if path == "":
            return {"id": 42}
        if base == sync.GITHUB_API:
            return release
        assert method == "GET"
        return {"id": 2} if existing else None

    monkeypatch.setattr(sync, "api_request", MagicMock(side_effect=api))
    monkeypatch.setattr(sync, "list_pages", lambda session, base, path: [source_asset] if base == sync.GITHUB_API else [{"id": 3, "name": NAME, "browser_download_url": "https://gitee.com/download/package.zip"}])

    def download(url, output, max_bytes, expected_size=None, **kwargs):
        if output:
            output.write_bytes(b"package")
            return DIGEST
        return "different" if conflict else DIGEST

    monkeypatch.setattr(sync, "download_hash", MagicMock(side_effect=download))
    monkeypatch.setattr(sync, "sync_code", MagicMock())
    monkeypatch.setattr(sync, "sync_release", MagicMock())
    return release


def test_main_dry_run_passes_preview_through_all_writes(tmp_path, monkeypatch):
    prepare_main(monkeypatch, tmp_path)
    sync.main()
    sync.sync_code.assert_not_called()
    assert sync.sync_release.call_args.args[4] is True
    assert len(sync.sync_release.call_args.args[2]) == 1
    assert "未修改 Gitee" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_retry_skips_identical_asset_without_code_sync(tmp_path, monkeypatch):
    prepare_main(monkeypatch, tmp_path, existing=True, dry_run=False)
    sync.main()
    assert sync.sync_release.call_args.args[2] == []
    sync.sync_code.assert_not_called()
    assert sync.download_hash.call_args.args[0] == "https://gitee.com/download/package.zip"


def test_conflict_aborts_before_code_or_release_writes(tmp_path, monkeypatch):
    prepare_main(monkeypatch, tmp_path, existing=True, conflict=True, dry_run=False)
    with pytest.raises(ValueError, match="同名附件内容不同"):
        sync.main()
    sync.sync_code.assert_not_called()
    sync.sync_release.assert_not_called()


@pytest.mark.parametrize("field,value", [("draft", True), ("prerelease", True), ("tag_name", "v1.2.3-test")])
def test_main_rejects_nonstable_release(tmp_path, monkeypatch, field, value):
    release = prepare_main(monkeypatch, tmp_path)
    release[field] = value
    with pytest.raises(ValueError, match="正式版"):
        sync.main()
    sync.sync_code.assert_not_called()


def test_code_only_does_not_read_releases(tmp_path, monkeypatch):
    prepare_main(monkeypatch, tmp_path, sync_code=True)
    sync.main()
    assert sync.api_request.call_count > 1
    sync.sync_code.assert_called_once()
    assert sync.sync_code.call_args.kwargs == {"tag": TAG}
    sync.sync_release.assert_called_once()


def test_default_release_sync_does_not_sync_code(tmp_path, monkeypatch):
    prepare_main(monkeypatch, tmp_path)
    sync.main()
    sync.sync_code.assert_not_called()


def test_code_switch_syncs_all_refs(tmp_path, monkeypatch):
    prepare_main(monkeypatch, tmp_path, sync_code=True)
    sync.main()
    sync.sync_code.assert_called_once()
    assert sync.sync_code.call_args.args[1:3] == ("fake-token", True)
    assert sync.sync_code.call_args.kwargs == {"tag": TAG}


@pytest.mark.parametrize("existing", [None, {"id": 2}])
def test_release_create_update_and_verify_upload(tmp_path, monkeypatch, existing):
    path = tmp_path / NAME
    path.write_bytes(b"package")
    api = MagicMock(return_value={"id": 2})
    monkeypatch.setattr(sync, "api_request", api)
    monkeypatch.setattr(sync, "list_pages", MagicMock(return_value=[{"id": 3, "name": NAME, "browser_download_url": "https://gitee.com/download/package.zip"}]))
    monkeypatch.setattr(sync, "download_hash", MagicMock(return_value=DIGEST))
    release = {"tag_name": TAG, "body": "$(do not execute)", "name": "Release"}
    sync.sync_release(MagicMock(), release, [(path, DIGEST)], existing, False)
    assert api.call_args_list[0].args[1] == ("PATCH" if existing else "POST")
    assert api.call_args_list[0].kwargs["data"]["body"] == "$(do not execute)"
    assert api.call_args_list[0].kwargs["data"]["prerelease"] is False
    assert "target_commitish" not in api.call_args_list[0].kwargs["data"]
    assert api.call_args_list[1].args[3] == "/releases/2/attach_files"
    assert sync.download_hash.call_count == 1
    assert sync.download_hash.call_args.args[0] == "https://gitee.com/download/package.zip"


def test_release_dry_run_never_calls_mutating_api(tmp_path, monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(sync, "api_request", api)
    sync.sync_release(MagicMock(), {"tag_name": TAG}, [(tmp_path / NAME, DIGEST)], None, True)
    api.assert_not_called()


def test_invalid_dry_run_value_fails_closed(monkeypatch):
    monkeypatch.setenv("SYNC_DRY_RUN", "True")
    with pytest.raises(ValueError, match="SYNC_DRY_RUN"):
        sync.main()
