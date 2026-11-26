#!/usr/bin/env python3
"""一次性全量验证：在单个登录会话内依次完成
1) 设备清单 2) 冰箱读+改温回滚 3) 炸锅只读 4) 摄像头批量只读
5) 扫地机/空调/门锁抽查 6) 摄像头云端路线探测（消息事件/云存/Google流配置）
任何一步遇到云端节流就记录并跳过，不硬刷。结果打印到标准输出。
"""
import asyncio
import json
import sys

import mcp_server as ms
import mi_ext
import miot_spec


def p(*a):
    print(*a, flush=True)


async def main():
    auth = await ms._get_auth()
    svc = ms.XiaomiIOService(auth)

    # 1) 设备清单
    try:
        devices = await mi_ext.load_raw_devices(force=True)
    except Exception as e:
        p(f"[清单] 失败: {e}")
        return
    p(f"[清单] 共 {len(devices)} 台，在线 {sum(1 for d in devices if d.get('isOnline'))} 台")
    fridges = [d for d in devices if "fridge" in str(d.get("model", "")).lower()]
    for d in fridges:
        p(f"  冰箱: {d.get('name')} | {d.get('model')} | did={d.get('did')} | 在线={d.get('isOnline')}")
    fryers = [d for d in devices if "fryer" in str(d.get("model", "")).lower()]
    for d in fryers:
        p(f"  炸锅: {d.get('name')} | {d.get('model')} | did={d.get('did')} | 在线={d.get('isOnline')}")
    cams = [d for d in devices if mi_ext.categorize(d.get("model", ""), d.get("name", "")) == "摄像头"]
    p(f"  摄像头 {len(cams)} 台:")
    for d in cams:
        p(f"    {d.get('name')} | {d.get('model')} | 在线={d.get('isOnline')}")

    # 2) 冰箱：读全部 + 冷藏目标温度 +1 再改回
    if fridges:
        d = fridges[0]
        did = str(d["did"])
        spec = miot_spec.get_spec(d["model"])
        try:
            allprops = await mi_ext.get_all_props(svc, did, spec)
            p(f"[冰箱] {d['name']} 全部属性:")
            for (s, pi), (prop, v) in sorted(allprops.items()):
                p("  " + mi_ext.fmt_prop_line(s, pi, prop, v))
            cur = allprops.get((3, 2), (None, None))[1]
            if isinstance(cur, (int, float)):
                newv = cur + 1 if cur < 8 else cur - 1
                r = await svc.miot_set_prop(did, [(3, 2, newv)])
                p(f"[冰箱] 冷藏目标温度 {cur} -> {newv}，返回 {json.dumps(r, ensure_ascii=False)[:200]}")
                back = await svc.miot_get_props(did, [(3, 2)])
                p(f"[冰箱] 回读: {back}")
                r2 = await svc.miot_set_prop(did, [(3, 2, cur)])
                back2 = await svc.miot_get_props(did, [(3, 2)])
                p(f"[冰箱] 已改回 {cur}，返回 {json.dumps(r2, ensure_ascii=False)[:120]}，回读: {back2}")
        except Exception as e:
            p(f"[冰箱] 出错: {e}")

    # 3) 空气炸锅：只读
    if fryers:
        d = fryers[0]
        spec = miot_spec.get_spec(d["model"])
        try:
            allprops = await mi_ext.get_all_props(svc, str(d["did"]), spec)
            p(f"[炸锅] {d['name']} 全部属性:")
            for (s, pi), (prop, v) in sorted(allprops.items()):
                p("  " + mi_ext.fmt_prop_line(s, pi, prop, v))
        except Exception as e:
            p(f"[炸锅] 出错: {e}")

    # 4) 摄像头：按 spec 挑关键属性，一次跨设备批量读
    jobs, meta = [], {}
    for d in cams:
        if not d.get("isOnline"):
            continue
        spec = miot_spec.get_spec(d["model"])
        if not spec:
            continue
        _, prop_defs, _ = miot_spec.spec_index(spec)
        for (s, pi), prop in prop_defs.items():
            t = (prop.get("type", "") + prop.get("description", "")).lower()
            if "read" in prop.get("access", []) and any(
                k in t for k in ("switch", "night", "record", "storage", "motion", "power", "on-off")
            ):
                jobs.append((str(d["did"]), s, pi))
                meta[(str(d["did"]), s, pi)] = (d["name"], prop)
    if jobs:
        try:
            vals = await mi_ext.batch_get_props(svc, jobs)
            p(f"[摄像头] 关键状态（共读到 {len(vals)} 项）:")
            cur = None
            for (did, s, pi) in jobs:
                if (did, s, pi) not in vals:
                    continue
                name, prop = meta[(did, s, pi)]
                if name != cur:
                    p(f"  == {name}")
                    cur = name
                p("   " + mi_ext.fmt_prop_line(s, pi, prop, vals[(did, s, pi)]))
        except Exception as e:
            p(f"[摄像头] 批量读出错: {e}")

    # 5) 抽查：扫地机/空调/门锁（只读几个关键属性）
    for kw, keys_desc in [("vacuum", "扫地机"), ("aircondition", "空调"), ("lock", "门锁")]:
        hits = [d for d in devices if kw in str(d.get("model", "")).lower() and d.get("isOnline")]
        for d in hits[:1]:
            spec = miot_spec.get_spec(d["model"])
            if not spec:
                p(f"[抽查] {d['name']} 无 spec")
                continue
            try:
                allprops = await mi_ext.get_all_props(svc, str(d["did"]), spec)
                p(f"[抽查-{keys_desc}] {d['name']}（{d['model']}）前 10 项:")
                for i, ((s, pi), (prop, v)) in enumerate(sorted(allprops.items())):
                    if i >= 10:
                        break
                    p("  " + mi_ext.fmt_prop_line(s, pi, prop, v))
            except Exception as e:
                p(f"[抽查-{keys_desc}] {d['name']} 出错: {e}")

    # 6) 摄像头云端路线探测
    p("[探测] 路线一：账号消息中心")
    try:
        msgs = await mi_ext.cam_message_events(svc, limit=5)
        p("  返回:", json.dumps(msgs, ensure_ascii=False)[:1200])
    except Exception as e:
        p(f"  失败: {str(e)[:200]}")
    if cams:
        d = next((x for x in cams if x.get("isOnline")), cams[0])
        p(f"[探测] 路线二：云存端点（对象 {d['name']}）")
        try:
            uri, r, tried = await mi_ext.cam_cloud_storage_probe(svc, str(d["did"]))
            for u, res in tried:
                p(f"  {u} -> {json.dumps(res, ensure_ascii=False)[:250] if not isinstance(res, str) else res}")
        except Exception as e:
            p(f"  失败: {str(e)[:200]}")
        # 路线三：Google 流配置动作（只读型动作，返回 out 参数）
        spec = miot_spec.get_spec(d["model"])
        if spec:
            _, _, act_defs = miot_spec.spec_index(spec)
            target = None
            for (s, a), v in sorted(act_defs.items()):
                if "Stream Configuration" in (v.get("description", "") or ""):
                    target = (s, a)
            if target:
                p(f"[探测] 路线三：流配置动作 {target}（对象 {d['name']}）")
                try:
                    result = await svc._request(
                        "/miotspec/action",
                        {"params": {"did": str(d["did"]), "siid": target[0], "aiid": target[1], "in": []}},
                    )
                    p("  返回:", json.dumps(result, ensure_ascii=False)[:1200])
                except Exception as e:
                    p(f"  失败: {str(e)[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
