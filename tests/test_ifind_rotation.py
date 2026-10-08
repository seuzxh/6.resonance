"""双账号令牌切换的离线测试（mock 令牌端点与请求，无网络、不碰真缓存）。

覆盖 2026-10-09 落地的三件事：
- 多 refresh token 有序加载（env 优先、去重、文件兜底）；
- 账号 id 从 access 尾缀 / refresh 中段解出，缓存黏性排序；
- 配额码出现时切换备用账号重试一次；refresh 失败按序回退。
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resonance import ifind  # noqa: E402


def _uid_b64(uid: str) -> str:
    return base64.b64encode(uid.encode()).decode()


ACC_A = f"aaaa.signs_{_uid_b64('63483625')}"
ACC_B = f"bbbb.signs_{_uid_b64('90384397')}"
RT_A = f"head.{_uid_b64('{"uid":"63483625"}')}.tail"  # noqa: F541 — 中段即账号
RT_B = f"head.{_uid_b64('{"uid":"90384397"}')}.tail"


def test_account_id_from_access_and_refresh():
    assert ifind._account_id(ACC_A) == "63483625"
    assert ifind._account_id(ACC_B) == "90384397"
    assert ifind._account_id(RT_B) == "90384397"
    assert ifind._account_id("not-a-token") is None


def test_load_refresh_tokens_env_order_and_dedup(monkeypatch):
    monkeypatch.setattr(ifind.config, "REFRESH_TOKEN_PATHS", ())
    monkeypatch.setenv("IFIND_REFRESH_TOKEN", RT_A)
    monkeypatch.setenv("IFIND_REFRESH_TOKEN_2", RT_B)
    assert ifind.load_refresh_tokens() == [RT_A, RT_B]
    monkeypatch.setenv("IFIND_REFRESH_TOKEN_2", RT_A)  # 重复 → 去重
    assert ifind.load_refresh_tokens() == [RT_A]
    assert ifind.load_refresh_token() == RT_A  # 旧契约：取首个


def test_load_refresh_tokens_from_file_source(monkeypatch, tmp_path):
    monkeypatch.delenv("IFIND_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("IFIND_REFRESH_TOKEN_2", raising=False)
    f = tmp_path / "env.sh"
    f.write_text(f'export IFIND_REFRESH_TOKEN="{RT_A}"\n'
                 f'export IFIND_REFRESH_TOKEN_2="{RT_B}"\n')
    monkeypatch.setattr(ifind.config, "REFRESH_TOKEN_PATHS", (f,))
    assert ifind.load_refresh_tokens() == [RT_A, RT_B]


def test_sticky_order_prefers_cached_account():
    ordered = ifind._sticky_order([RT_A, RT_B], "90384397")
    assert ordered == [RT_B, RT_A]
    assert ifind._sticky_order([RT_A, RT_B], None) == [RT_A, RT_B]


def test_post_quota_rotates_to_backup(monkeypatch):
    """首个账号 -4302 → 切换备用账号重试成功。"""
    calls = []

    def fake_post(url, json=None, headers=None, timeout=120):
        calls.append(headers.get("access_token"))
        if len(calls) == 1:
            return type("R", (), {"status_code": 200, "json": staticmethod(
                lambda: {"errorcode": -4302, "errmsg": "quota"})})()
        return type("R", (), {"status_code": 200, "json": staticmethod(
            lambda: {"errorcode": 0, "tables": []})})()

    rotated = []
    monkeypatch.setattr(ifind.requests, "post", fake_post)
    monkeypatch.setattr(ifind, "get_access_token", lambda: ACC_A)
    monkeypatch.setattr(ifind, "_rotate_on_quota",
                        lambda cur: rotated.append(cur) or ACC_B)
    out = ifind._post("http://x", {"reqBody": {}})
    assert out["errorcode"] == 0
    assert calls == [ACC_A, ACC_B]  # 第二次请求确实用了备用账号
    assert rotated == [ACC_A]


def test_post_quota_without_backup_raises(monkeypatch):
    monkeypatch.setattr(ifind.requests, "post", lambda *a, **k: type(
        "R", (), {"status_code": 200, "json": staticmethod(
            lambda: {"errorcode": -4302, "errmsg": "quota"})})())
    monkeypatch.setattr(ifind, "get_access_token", lambda: ACC_A)
    monkeypatch.setattr(ifind, "_rotate_on_quota", lambda cur: None)
    with pytest.raises(ifind.IfindError, match="配额耗尽"):
        ifind._post("http://x", {"reqBody": {}})


def test_get_access_token_refresh_fallback(monkeypatch):
    """缓存过期后按黏性序 refresh：首选账号令牌失效 → 自动回退下一个。"""
    monkeypatch.setattr(ifind, "_read_token_cache", lambda: (ACC_A, "2000-01-01 00:00:00"))
    monkeypatch.setattr(ifind, "load_refresh_tokens", lambda: [RT_A, RT_B])

    def fake_refresh(refresh=None):
        if refresh is RT_A:
            raise ifind.IfindError("token refresh failed: errorcode=-1001 expired")
        return ACC_B

    monkeypatch.setattr(ifind, "_refresh_access_token", fake_refresh)
    assert ifind.get_access_token() == ACC_B
