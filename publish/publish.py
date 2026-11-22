# -*- coding: utf-8 -*-
"""GitHub Actionsから毎日21:00（JST）に走る投稿スクリプト

plan.json のその日の予定を見て、カルーセルかリールをInstagramへ投稿する。
画像と動画はこのリポジトリのraw URLをMetaに渡すだけなので、実行時のアップロードは無い。

環境変数：META_ACCESS_TOKEN / INSTAGRAM_ACCOUNT_ID（GitHubのSecrets）
引数 --dry-run でコンテナ作成まで（公開しない）
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

JST = timezone(timedelta(hours=9))
API = "https://graph.facebook.com/v21.0"
ROOT = Path(__file__).resolve().parent.parent
PLAN = json.loads((ROOT / "plan.json").read_text(encoding="utf-8"))
TOKEN = os.environ["META_ACCESS_TOKEN"]
IG = os.environ["INSTAGRAM_ACCOUNT_ID"]
DRY = "--dry-run" in sys.argv


def call(method, path, **params):
    """一時的な通信エラーで落ちないように再試行する"""
    params["access_token"] = TOKEN
    last = None
    for attempt in range(5):
        try:
            if method == "GET":
                r = requests.get(f"{API}/{path}", params=params, timeout=90)
            else:
                r = requests.post(f"{API}/{path}", data=params, timeout=180)
            if r.status_code == 200:
                return r.json()
            body = r.text[:300]
            # 4xxのうち再試行で直らないものは即あきらめる
            if 400 <= r.status_code < 500 and "rate limit" not in body.lower():
                raise RuntimeError(f"{path}: {r.status_code} {body}")
            last = RuntimeError(f"{path}: {r.status_code} {body}")
        except requests.RequestException as e:
            last = e
        wait = 5 * (attempt + 1)
        print(f"  再試行します（{attempt + 1}回目／{wait}秒待機）: {last}")
        time.sleep(wait)
    raise last


def already_posted_today(today):
    d = call("GET", f"{IG}/media", fields="timestamp", limit=3)
    for m in d.get("data", []):
        ts = datetime.fromisoformat(m["timestamp"].replace("+0000", "+00:00"))
        if ts.astimezone(JST).date() == today:
            return True
    return False


def url_of(path):
    return PLAN["repo_raw"].rstrip("/") + "/" + path.lstrip("/")


def wait_finished(cid, tries=40):
    for _ in range(tries):
        st = call("GET", cid, fields="status_code")
        code = st.get("status_code")
        if code == "FINISHED":
            return
        if code == "ERROR":
            raise RuntimeError(f"コンテナがエラーになりました: {st}")
        time.sleep(6)
    raise RuntimeError("処理が終わりませんでした")


def publish_carousel(item):
    children = []
    for p in item["images"]:
        d = call("POST", f"{IG}/media", image_url=url_of(p), is_carousel_item="true")
        children.append(d["id"])
    print(f"  画像コンテナ {len(children)}件")
    car = call("POST", f"{IG}/media", media_type="CAROUSEL",
               children=",".join(children), caption=item["caption"])
    wait_finished(car["id"])
    return car["id"]


def publish_reel(item):
    cont = call("POST", f"{IG}/media", media_type="REELS",
                video_url=url_of(item["video"]), cover_url=url_of(item["cover"]),
                caption=item["caption"], share_to_feed="false", audio_name="withWine")
    print("  動画の処理を待ちます")
    wait_finished(cont["id"])
    return cont["id"]


def main():
    today = datetime.now(JST).date()
    item = next((x for x in PLAN["items"] if x["date"] == today.isoformat()), None)
    if not item:
        print(f"{today} は投稿予定なし")
        return 0
    print(f"{today} {item['kind']} {item['folder']}")
    if already_posted_today(today):
        print("すでに今日の投稿があるため中止します")
        return 0
    cid = publish_carousel(item) if item["kind"] == "carousel" else publish_reel(item)
    if DRY:
        print(f"--dry-run のため公開していません（creation_id={cid}）")
        return 0
    res = call("POST", f"{IG}/media_publish", creation_id=cid)
    link = call("GET", res["id"], fields="permalink")
    print("投稿しました:", link.get("permalink"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("NG 投稿に失敗しました:", e)
        sys.exit(1)
