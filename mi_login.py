#!/usr/bin/env python3
"""小米扫码登录 - 生成二维码图片供用户扫描"""
import asyncio
import sys
sys.path.insert(0, "/home/hatch/workspace/xiaomi-mcp")

from login import login_qrcode, _parse_json, UA, SID
import aiohttp
import json
import time
from hashlib import md5
from pathlib import Path
from urllib.parse import quote, urlencode


async def main():
    device_id = md5(f"xiaomi-mcp-qr-{int(time.time())}".encode()).hexdigest()[:16]

    async with aiohttp.ClientSession() as session:
        print("[1/3] 获取二维码...")
        qr_params = urlencode(
            {
                "_qrsize": "480",
                "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
                "callback": "https://sts.api.io.mi.com/sts",
                "_hasLogo": "false",
                "sid": SID,
                "serviceParam": "",
                "_locale": "zh_CN",
                "_dc": str(int(time.time() * 1000)),
            },
            quote_via=quote,
        )
        async with session.get(
            f"https://account.xiaomi.com/longPolling/loginUrl?{qr_params}",
            headers={"User-Agent": UA},
            cookies={"sdkVersion": "accountsdk-18.8.15", "deviceId": device_id},
        ) as resp:
            text = (await resp.read()).decode("utf-8")
            result = _parse_json(text)

        qr_url = result.get("qr")
        lp_url = result.get("lp")
        timeout = result.get("timeout", 300)

        if not qr_url or not lp_url:
            print(f"获取二维码失败: {result}")
            return

        # 下载二维码图片到固定位置
        async with session.get(qr_url) as resp:
            img_data = await resp.read()
            qr_path = Path("/home/hatch/workspace/xiaomi-mcp/mi_qrcode.png")
            qr_path.write_bytes(img_data)
            print(f"QRCODE_SAVED:{qr_path}")

        # 等待扫码
        print("[2/3] 等待扫码...（米家 App → 我的 → 右上角扫一扫）")
        start_time = time.time()
        login_result = None

        while time.time() - start_time < timeout:
            try:
                async with session.get(
                    lp_url,
                    headers={"User-Agent": UA},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        text = (await resp.read()).decode("utf-8")
                        login_result = _parse_json(text)
                        if login_result.get("passToken"):
                            break
                        lp_url_new = login_result.get("lp")
                        if lp_url_new:
                            lp_url = lp_url_new
            except asyncio.TimeoutError:
                continue
            except Exception:
                continue

        if login_result and login_result.get("passToken"):
            from login import save_tokens
            tokens = {
                "userId": login_result.get("userId"),
                "passToken": login_result.get("passToken"),
                "deviceId": device_id,
            }
            save_tokens(tokens)
            print("LOGIN_SUCCESS")
        else:
            print("LOGIN_FAILED: 二维码过期或未扫码")


if __name__ == "__main__":
    asyncio.run(main())
