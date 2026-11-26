#!/usr/bin/env python3
"""米家全功能 CLI（扩展版）。

用法:
  mix.py devices [--refresh]            设备清单（名称/型号/房间/在线）
  mix.py spec <设备>                    该设备的全部属性与动作（按 miot-spec）
  mix.py get <设备> [siid piid]         读全部可读属性，或读单个属性
  mix.py set <设备> <siid> <piid> <值>  写属性（按 spec 校验类型与可写性）
  mix.py do <设备> <siid> <aiid> [参数json]  调用动作
  mix.py fridge [status]                冰箱状态（全部属性）
  mix.py fridge set <关键词> <值>       按关键词改冰箱可写属性，如 set 冷冻 -18
  mix.py airfryer [status]              空气炸锅状态
  mix.py airfryer stop                  取消烹饪（调用 spec 的取消动作）
  mix.py cameras                        摄像头清单与关键状态
  mix.py cam-probe <摄像头>             摄像头云端能力探测（事件消息/云存/spec动作）
  mix.py report [--refresh]             生成家庭状态报告 Markdown

安全护栏：门锁类设备禁止写属性/调动作（只读）。
"""
import asyncio
import json
import sys

import mcp_server as ms
import mi_ext
import miot_spec


async def _svc():
    auth = await ms._get_auth()
    return ms.XiaomiIOService(auth)


def _guard_writable(d):
    if "lock" in str(d.get("model", "")).lower() or "门锁" in str(d.get("name", "")):
        raise Exception("门锁类设备只读，禁止写操作")


async def cmd_devices(args):
    force = "--refresh" in args
    devices = await mi_ext.load_raw_devices(force=force)
    print(f"共 {len(devices)} 台设备：")
    for d in devices:
        state = "在线" if d.get("isOnline") else "离线"
        cat = mi_ext.categorize(d.get("model", ""), d.get("name", ""))
        print(f"  [{cat}] {d.get('name')} | {d.get('model')} | {mi_ext.device_room(d)} | {state} | did={d.get('did')}")


async def cmd_spec(args):
    devices = await mi_ext.load_raw_devices()
    d = mi_ext.find_one(devices, args[0])
    spec = miot_spec.get_spec(d["model"])
    if not spec:
        print(f"{d['name']}（{d['model']}）：miot-spec.org 无此型号的公开 spec")
        return
    services, props, actions = miot_spec.spec_index(spec)
    print(f"{d['name']}（{d['model']}）spec：")
    for (s, p), v in sorted(props.items()):
        acc = "/".join(v.get("access", []))
        print(f"  属性 {s}.{p} [{services.get(s, '')}] {v.get('description', '')} type={v.get('type')} access={acc}")
    for (s, a), v in sorted(actions.items()):
        print(f"  动作 {s}.{a} [{services.get(s, '')}] {v.get('description', '')} in={v.get('in')} out={v.get('out')}")


async def cmd_get(args):
    devices = await mi_ext.load_raw_devices()
    d = mi_ext.find_one(devices, args[0])
    svc = await _svc()
    spec = miot_spec.get_spec(d["model"])
    if len(args) >= 3:
        siid, piid = int(args[1]), int(args[2])
        vals = await svc.miot_get_props(str(d["did"]), [(siid, piid)])
        prop = {}
        if spec:
            _, prop_defs, _ = miot_spec.spec_index(spec)
            prop = prop_defs.get((siid, piid), {})
        print(mi_ext.fmt_prop_line(siid, piid, prop, vals[0] if vals else None))
        return
    if not spec:
        print("无公开 spec，无法枚举属性；可用 get <设备> <siid> <piid> 直读")
        return
    allprops = await mi_ext.get_all_props(svc, str(d["did"]), spec)
    print(f"{d['name']}（{d['model']}）全部可读属性：")
    for (s, p), (prop, v) in sorted(allprops.items()):
        print(mi_ext.fmt_prop_line(s, p, prop, v))


async def cmd_set(args):
    devices = await mi_ext.load_raw_devices()
    d = mi_ext.find_one(devices, args[0])
    _guard_writable(d)
    siid, piid, raw = int(args[1]), int(args[2]), args[3]
    spec = miot_spec.get_spec(d["model"])
    prop = {}
    if spec:
        _, prop_defs, _ = miot_spec.spec_index(spec)
        prop = prop_defs.get((siid, piid))
        if prop is None:
            raise Exception(f"spec 中不存在属性 {siid}.{piid}")
        if "write" not in prop.get("access", []):
            raise Exception(f"属性 {siid}.{piid}（{prop.get('description')}）不可写")
    value = miot_spec.coerce_value(prop, raw)
    svc = await _svc()
    result = await svc.miot_set_prop(str(d["did"]), [(siid, piid, value)])
    print(f"已写入 {d['name']} {siid}.{piid} = {value}，云端返回: {json.dumps(result, ensure_ascii=False)}")
    vals = await svc.miot_get_props(str(d["did"]), [(siid, piid)])
    print(f"回读确认: {siid}.{piid} = {vals[0] if vals else None}")


async def cmd_do(args):
    devices = await mi_ext.load_raw_devices()
    d = mi_ext.find_one(devices, args[0])
    _guard_writable(d)
    siid, aiid = int(args[1]), int(args[2])
    params = json.loads(args[3]) if len(args) > 3 else []
    spec = miot_spec.get_spec(d["model"])
    if spec:
        _, _, act_defs = miot_spec.spec_index(spec)
        if (siid, aiid) not in act_defs:
            raise Exception(f"spec 中不存在动作 {siid}.{aiid}")
    svc = await _svc()
    code = await svc.miot_action(str(d["did"]), [siid, aiid], params)
    print(f"动作 {d['name']} {siid}.{aiid} 返回 code={code}（0=成功）")


async def _friendly_status(query):
    devices = await mi_ext.load_raw_devices()
    d = mi_ext.find_one(devices, query)
    svc = await _svc()
    spec = miot_spec.get_spec(d["model"])
    print(f"{d['name']}（{d['model']}，{'在线' if d.get('isOnline') else '离线'}）")
    if not spec:
        print("无公开 spec")
        return d, None, None
    allprops = await mi_ext.get_all_props(svc, str(d["did"]), spec)
    for (s, p), (prop, v) in sorted(allprops.items()):
        print(mi_ext.fmt_prop_line(s, p, prop, v))
    return d, svc, spec


async def cmd_fridge(args):
    if len(args) >= 3 and args[0] == "set":
        kw, raw = args[1], args[2]
        devices = await mi_ext.load_raw_devices()
        d = mi_ext.find_one(devices, "冰箱")
        spec = miot_spec.get_spec(d["model"])
        writable = miot_spec.writable_props(spec)
        hits = [(s, p, v) for s, p, v in writable if kw in (v.get("description", "") or "")]
        if not hits:
            opts = "、".join(f"{s}.{p} {v.get('description')}" for s, p, v in writable)
            raise Exception(f"没有描述含「{kw}」的可写属性。可写属性有：{opts}")
        s, p, prop = hits[0]
        value = miot_spec.coerce_value(prop, raw)
        svc = await _svc()
        result = await svc.miot_set_prop(str(d["did"]), [(s, p, value)])
        print(f"已设置 {d['name']} {s}.{p} {prop.get('description')} = {value}，返回: {json.dumps(result, ensure_ascii=False)}")
        vals = await svc.miot_get_props(str(d["did"]), [(s, p)])
        print(f"回读确认: {vals[0] if vals else None}")
        return
    await _friendly_status("冰箱")


async def cmd_airfryer(args):
    if args and args[0] == "stop":
        devices = await mi_ext.load_raw_devices()
        d = mi_ext.find_one(devices, "空气炸锅")
        spec = miot_spec.get_spec(d["model"])
        _, _, act_defs = miot_spec.spec_index(spec)
        target = None
        for (s, a), v in sorted(act_defs.items()):
            desc = (v.get("description", "") or "").lower()
            if any(k in desc for k in ("cancel", "stop", "取消", "停止")):
                target = (s, a, v.get("description"))
                break
        if not target:
            raise Exception("该型号 spec 里没有取消/停止动作")
        svc = await _svc()
        code = await svc.miot_action(str(d["did"]), [target[0], target[1]], [])
        print(f"已调用取消动作 {target[0]}.{target[1]}（{target[2]}），code={code}")
        return
    await _friendly_status("空气炸锅")


async def cmd_cameras(args):
    devices = await mi_ext.load_raw_devices()
    cams = [d for d in devices if mi_ext.categorize(d.get("model", ""), d.get("name", "")) == "摄像头"]
    print(f"共 {len(cams)} 台摄像头：")
    svc = await _svc()
    for d in cams:
        print(f"  {d.get('name')} | {d.get('model')} | {mi_ext.device_room(d)} | {'在线' if d.get('isOnline') else '离线'}")
        if d.get("isOnline"):
            spec = miot_spec.get_spec(d["model"])
            if spec:
                allprops = await mi_ext.get_all_props(svc, str(d["did"]), spec)
                for (s, p), (prop, v) in sorted(allprops.items()):
                    print("   " + mi_ext.fmt_prop_line(s, p, prop, v))


async def cmd_cam_probe(args):
    devices = await mi_ext.load_raw_devices()
    d = mi_ext.find_one(devices, args[0])
    print(f"探测对象：{d['name']}（{d['model']}，did={d['did']}）")
    spec = miot_spec.get_spec(d["model"])
    if spec:
        _, _, act_defs = miot_spec.spec_index(spec)
        print(f"spec 动作共 {len(act_defs)} 个：")
        for (s, a), v in sorted(act_defs.items()):
            print(f"  动作 {s}.{a} {v.get('description', '')} in={v.get('in')} out={v.get('out')}")
    svc = await _svc()
    print("\n— 路线一：账号消息中心（看家事件）—")
    try:
        msgs = await mi_ext.cam_message_events(svc, limit=10)
        print(json.dumps(msgs, ensure_ascii=False)[:1500])
    except Exception as e:
        print("失败：", str(e)[:300])
    print("\n— 路线二：云存录像列表端点 —")
    uri, r, tried = await mi_ext.cam_cloud_storage_probe(svc, str(d["did"]))
    for u, res in tried:
        print(f"  {u} -> {json.dumps(res, ensure_ascii=False)[:300] if not isinstance(res, str) else res}")
    if uri:
        print(f"命中端点：{uri}")


async def cmd_report(args):
    path = await mi_ext.save_report(force_refresh="--refresh" in args)
    print(f"报告已生成：{path}")


COMMANDS = {
    "devices": cmd_devices,
    "spec": cmd_spec,
    "get": cmd_get,
    "set": cmd_set,
    "do": cmd_do,
    "fridge": cmd_fridge,
    "airfryer": cmd_airfryer,
    "cameras": cmd_cameras,
    "cam-probe": cmd_cam_probe,
    "report": cmd_report,
}


async def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        return
    try:
        await COMMANDS[sys.argv[1]](sys.argv[2:])
    except mi_ext.ThrottleError as e:
        print(f"⚠️ {e}")
    except Exception as e:
        print(f"出错: {e}")


if __name__ == "__main__":
    asyncio.run(main())
