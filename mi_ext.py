#!/usr/bin/env python3
"""米家全功能扩展：在 mcp_server 的云端客户端之上提供
- 通用 MIoT 读写/动作（按 miot-spec 自动发现每台设备的全部属性与动作）
- 冰箱 / 空气炸锅 / 摄像头友好命令
- 摄像头云端路线探测（事件消息、云存）
- 一键家庭状态报告（Markdown）

设备清单缓存 ~/.xiaomi-mcp/devices_cache.json，减少云端调用（小米云对
device_list 有节流，返回 wait_second 时本模块会明确报错而不是假装成功）。
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mcp_server as ms
import miot_spec

CACHE_FILE = os.path.expanduser("~/.xiaomi-mcp/devices_cache.json")
CACHE_TTL = 600  # 秒
REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


class ThrottleError(Exception):
    pass


def _check_throttle(result):
    if isinstance(result, dict) and "wait_second" in result:
        raise ThrottleError(
            f"小米云端节流中（要求等待 {result.get('wait_second')} 秒）"
            "，请稍后再试；连续猛刷会延长节流。"
        )
    return result


# ── 设备清单 ────────────────────────────────────────────

async def load_raw_devices(force=False):
    """返回原始 device_list（含房间等全部字段），带本地缓存。"""
    if not force and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                cache = json.load(f)
            if time.time() - cache.get("ts", 0) < CACHE_TTL:
                return cache["devices"]
        except (json.JSONDecodeError, KeyError):
            pass
    auth = await ms._get_auth()
    svc = ms.XiaomiIOService(auth)
    result = await svc._request(
        "/home/device_list", {"getVirtualModel": False, "getHuamiDevices": 0}
    )
    _check_throttle(result)
    devices = result["list"]
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump({"ts": time.time(), "devices": devices}, f, ensure_ascii=False)
    return devices


def device_room(d: dict) -> str:
    if d.get("room_name"):
        return d["room_name"]
    extra = d.get("extra") or {}
    if isinstance(extra, dict) and extra.get("room_name"):
        return extra["room_name"]
    return "未分房间"


def find_devices(devices, query: str):
    """按名称子串/型号子串找设备，返回原始 dict 列表。"""
    q = query.lower()
    return [
        d for d in devices
        if q in str(d.get("name", "")).lower() or q in str(d.get("model", "")).lower()
    ]


def find_one(devices, query: str) -> dict:
    hits = find_devices(devices, query)
    if not hits:
        raise Exception(f"找不到设备: {query}")
    online = [d for d in hits if d.get("isOnline")]
    return (online or hits)[0]


def categorize(model: str, name: str) -> str:
    m = (model or "").lower()
    n = name or ""
    if "fridge" in m or "冰箱" in n:
        return "冰箱"
    if "camera" in m or "摄像" in n:
        return "摄像头"
    if "airfryer" in m or "空气炸锅" in n:
        return "空气炸锅"
    if "aircondition" in m or "acpartner" in m or "空调" in n:
        return "空调"
    if "light" in m or "lamp" in m or "灯" in n:
        return "灯光"
    if "vacuum" in m or "扫地" in n or "拖地" in n:
        return "扫地机"
    if "fan" in m or "风扇" in n:
        return "风扇"
    if "heater" in m or "电暖" in n or "取暖" in n:
        return "取暖"
    if "wifispeaker" in m or "音箱" in n or "小爱" in n:
        return "音箱"
    if "lock" in m or "门锁" in n:
        return "门锁"
    if "curtain" in m or "窗帘" in n:
        return "窗帘"
    if "sensor" in m or "传感" in n:
        return "传感器"
    if "plug" in m or "socket" in m or "插座" in n or "插排" in n:
        return "插座"
    if "kettle" in m or "水壶" in n:
        return "水壶"
    if "purifier" in m or "净化" in n:
        return "空气净化"
    if "humidifier" in m or "加湿" in n:
        return "加湿器"
    if "washer" in m or "洗衣" in n:
        return "洗衣机"
    if "tv" in m or "电视" in n:
        return "电视"
    if "router" in m or "路由" in n:
        return "路由器"
    return "其他"


# ── 通用 MIoT 读写 ──────────────────────────────────────

async def get_all_props(svc, did: str, spec: dict):
    """读一台设备的全部可读属性，返回 {(siid,piid): (prop定义, 值)}。"""
    readable = miot_spec.readable_props(spec)
    out = {}
    keys = [(s, p) for s, p, _ in readable]
    defs = {(s, p): v for s, p, v in readable}
    for i in range(0, len(keys), 20):
        chunk = keys[i:i + 20]
        values = await svc.miot_get_props(did, chunk)
        for (s, p), v in zip(chunk, values):
            out[(s, p)] = (defs[(s, p)], v)
        if i + 20 < len(keys):
            await asyncio.sleep(0.6)
    return out


async def batch_get_props(svc, jobs):
    """跨设备批量读属性。jobs: [(did, siid, piid)]，返回 {(did,siid,piid): value}。"""
    out = {}
    for i in range(0, len(jobs), 40):
        chunk = jobs[i:i + 40]
        params = [{"did": d, "siid": s, "piid": p} for d, s, p in chunk]
        result = await svc._request("/miotspec/prop/get", {"params": params})
        _check_throttle(result)
        for item in result:
            if item.get("code", 0) == 0:
                out[(str(item["did"]), item["siid"], item["piid"])] = item.get("value")
        if i + 40 < len(jobs):
            await asyncio.sleep(0.8)
    return out


def fmt_prop_line(siid, piid, prop, val) -> str:
    desc = prop.get("description", "") or prop.get("type", "")
    unit = prop.get("unit", "") or ""
    shown = miot_spec.value_label(prop, val)
    rng = prop.get("value-range")
    extra = f" [{rng[0]}~{rng[1]}]" if rng and "write" in prop.get("access", []) else ""
    return f"  {siid}.{piid} {desc}: {shown}{unit}{extra}"


# ── 摄像头云端路线探测 ──────────────────────────────────

async def cam_message_events(svc, limit=10):
    """路线一：账号消息中心（摄像头看家事件会以消息形式出现，带缩略图链接）。"""
    result = await svc._request(
        "/v2/message/v2/user_profile_messages",
        {"limit": limit, "user_type": 0, "needSort": 1},
    )
    _check_throttle(result)
    return result


async def cam_cloud_storage_probe(svc, did: str):
    """路线二：云存录像列表端点探测。逐个试已知候选路径，返回 (路径, 原始结果)。"""
    candidates = [
        ("/v2/camera/cloud_storage/list", {"did": did, "limit": 10}),
        ("/v2/camera/cloud-storage/list", {"did": did, "limit": 10}),
        ("/camera/cloudstorage/list", {"did": did, "limit": 10}),
    ]
    tried = []
    for uri, data in candidates:
        try:
            r = await svc._request(uri, data)
            tried.append((uri, r))
            if isinstance(r, dict) and "wait_second" not in r:
                return uri, r, tried
        except Exception as e:
            tried.append((uri, f"ERR {str(e)[:120]}"))
        await asyncio.sleep(1)
    return None, None, tried


# ── 家庭状态报告 ────────────────────────────────────────

async def build_report(force_refresh=False) -> str:
    devices = await load_raw_devices(force=force_refresh)
    auth = await ms._get_auth()
    svc = ms.XiaomiIOService(auth)

    # 先拉全部 spec（公开接口，不走小米云），再组批量读任务
    specs = {}
    jobs = []
    per_device_keys = {}
    for d in devices:
        did = str(d["did"])
        model = d.get("model", "")
        spec = None
        try:
            spec = miot_spec.get_spec(model)
        except Exception:
            spec = None
        specs[did] = spec
        keys = []
        if spec and d.get("isOnline"):
            for s, p, v in miot_spec.readable_props(spec):
                keys.append((s, p))
                jobs.append((did, s, p))
        per_device_keys[did] = keys

    values = {}
    throttle_note = ""
    if jobs:
        try:
            values = await batch_get_props(svc, jobs)
        except ThrottleError as e:
            throttle_note = f"\n> ⚠️ 生成时云端节流（{e}），部分设备状态未读到，稍后重新生成即可补全。\n"

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    online_n = sum(1 for d in devices if d.get("isOnline"))
    lines = [
        f"# 家庭状态报告",
        f"",
        f"生成时间：{now}｜设备共 {len(devices)} 台，在线 {online_n} 台",
        throttle_note,
        f"",
    ]
    by_room = {}
    for d in devices:
        by_room.setdefault(device_room(d), []).append(d)
    for room in sorted(by_room.keys()):
        lines.append(f"## {room}")
        lines.append("")
        for d in sorted(by_room[room], key=lambda x: categorize(x.get("model", ""), x.get("name", ""))):
            did = str(d["did"])
            cat = categorize(d.get("model", ""), d.get("name", ""))
            state = "在线" if d.get("isOnline") else "离线"
            lines.append(f"### {d.get('name', '?')}（{cat}，{state}）")
            lines.append(f"型号：{d.get('model', '?')}")
            spec = specs.get(did)
            if not d.get("isOnline"):
                lines.append("状态：离线，未读取")
            elif not spec:
                lines.append("状态：未找到该型号的公开 spec，仅知在线状态")
            else:
                _, prop_defs, _ = miot_spec.spec_index(spec)
                shown = 0
                for (s, p) in per_device_keys[did]:
                    v = values.get((did, s, p))
                    if v is None:
                        continue
                    lines.append(fmt_prop_line(s, p, prop_defs[(s, p)], v))
                    shown += 1
                    if shown >= 14:
                        lines.append("  …（更多属性见 spec 命令）")
                        break
                if shown == 0:
                    lines.append("状态：在线，但云端未返回可读属性")
            lines.append("")
    return "\n".join(lines)


async def save_report(force_refresh=False) -> str:
    text = await build_report(force_refresh=force_refresh)
    os.makedirs(REPORT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    path = os.path.join(REPORT_DIR, f"home-report-{stamp}.md")
    with open(path, "w") as f:
        f.write(text)
    latest = os.path.join(REPORT_DIR, "home-report-latest.md")
    with open(latest, "w") as f:
        f.write(text)
    return path
