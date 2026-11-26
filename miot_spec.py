#!/usr/bin/env python3
"""MIoT spec 工具：按型号从 miot-spec.org 拉取设备规范（属性/动作定义），本地缓存。

不依赖小米云端登录，只用公开的 spec 库。spec 缓存在 ~/.xiaomi-mcp/spec_cache/。
"""
import json
import os
import urllib.request

CACHE_DIR = os.path.expanduser("~/.xiaomi-mcp/spec_cache")
INSTANCES_URL = "https://miot-spec.org/miot-spec-v2/instances?status=released"
INSTANCE_URL = "https://miot-spec.org/miot-spec-v2/instance?type={urn}&language=zh-Hans"


def _fetch_json(url: str, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, name)


def model_to_urn(model: str) -> str | None:
    """model（如 chuangmi.camera.021a04）-> spec urn。结果缓存。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    idx_file = _cache_path("instances.json")
    if os.path.exists(idx_file):
        with open(idx_file) as f:
            instances = json.load(f)
    else:
        data = _fetch_json(INSTANCES_URL)
        instances = data.get("instances", data if isinstance(data, list) else [])
        with open(idx_file, "w") as f:
            json.dump(instances, f)
    suffix = model.split(".", 1)[-1]  # 去掉品牌前缀，如 camera.021a04
    brand = model.split(".")[0]
    last = model.rsplit(".", 1)[-1]
    seg = f"{brand}-{last}"  # urn 里的型号段，如 xiaomi-c16
    candidates = []
    for inst in instances:
        urn = inst.get("type", "")
        if seg in urn or model in urn or urn.endswith(":" + suffix.replace(".", ":") + ":1") or suffix in urn:
            candidates.append((inst.get("version", 0), urn))
    if not candidates:
        # 宽松匹配：urn 里包含 model 的最后一段
        tail = model.rsplit(".", 1)[-1]
        for inst in instances:
            urn = inst.get("type", "")
            if tail in urn and model.split(".")[1] in urn:
                candidates.append((inst.get("version", 0), urn))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def get_spec(model: str) -> dict | None:
    """返回某型号的完整 spec（服务/属性/动作），带本地缓存。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    safe = model.replace("/", "_")
    f = _cache_path(safe + ".json")
    if os.path.exists(f):
        with open(f) as fh:
            return json.load(fh)
    urn = model_to_urn(model)
    if not urn:
        return None
    spec = _fetch_json(INSTANCE_URL.format(urn=urllib.request.quote(urn, safe=":")))
    with open(f, "w") as fh:
        json.dump(spec, fh, ensure_ascii=False)
    return spec


def spec_index(spec: dict):
    """把 spec 整理成 {(siid,piid): prop}、{(siid,aiid): action}、{siid: service}。"""
    props, actions, services = {}, {}, {}
    for svc in spec.get("services", []):
        siid = svc.get("iid")
        services[siid] = svc.get("description", "")
        for p in svc.get("properties", []):
            props[(siid, p.get("iid"))] = p
        for a in svc.get("actions", []):
            actions[(siid, a.get("iid"))] = a
    return services, props, actions


def readable_props(spec: dict):
    _, props, _ = spec_index(spec)
    return [(s, p, v) for (s, p), v in props.items() if "read" in v.get("access", [])]


def writable_props(spec: dict):
    _, props, _ = spec_index(spec)
    return [(s, p, v) for (s, p), v in props.items() if "write" in v.get("access", [])]


def coerce_value(prop: dict, raw: str):
    """按 spec 的类型把命令行字符串转成 JSON 值。"""
    t = prop.get("type", "string")
    if t == "bool":
        return raw.lower() in ("1", "true", "on", "yes", "开")
    if t in ("uint8", "uint16", "uint32", "int8", "int16", "int32", "int64"):
        return int(raw)
    if t == "float":
        return float(raw)
    return raw


def value_label(prop: dict, val):
    """枚举值翻译成人话。"""
    for item in prop.get("value-list", []) or []:
        if item.get("value") == val:
            return f"{val}({item.get('description', '')})"
    return val
