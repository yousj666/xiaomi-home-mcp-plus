#!/usr/bin/env python3
"""零依赖小米云探针：passToken 登录 + 签名调用 /home/device_list。
用法: xm_probe.py <tokens.json路径>
只打印设备数量与前几台的 name/model，不打印任何凭证。
"""
import json
import sys
import time
import urllib.request
import urllib.parse
from base64 import b64decode, b64encode
from hashlib import sha1, sha256
from hmac import new as hmac_new
from os import urandom
import http.cookiejar

UA_APP = "APP/com.xiaomi.mihome APPV/6.0.103 iosPassportSDK/3.9.0 iOS/14.4 miHSTS"
UA_API = "iOS-14.4-6.0.103-iPhone12,3--D7744744F7AF32F0544445285880DD63E47D9BE9-8816080-84A3F44E137B71AE-iPhone"


def service_login(tokens):
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    cookies = {
        "sdkVersion": "3.9",
        "deviceId": tokens["deviceId"],
        "userId": str(tokens["userId"]),
        "passToken": tokens["passToken"],
    }
    cookie_hdr = "; ".join(f"{k}={v}" for k, v in cookies.items())
    url = "https://account.xiaomi.com/pass/serviceLogin?sid=xiaomiio&_json=true"
    req = urllib.request.Request(url, headers={"User-Agent": UA_APP, "Cookie": cookie_hdr})
    with opener.open(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    resp = json.loads(raw[11:])
    if resp.get("code") != 0:
        raise Exception(f"serviceLogin failed: code={resp.get('code')} desc={resp.get('desc')}")
    nsec = "nonce=" + str(resp["nonce"]) + "&" + resp["ssecurity"]
    client_sign = b64encode(sha1(nsec.encode()).digest()).decode()
    loc = resp["location"] + "&clientSign=" + urllib.parse.quote(client_sign)
    req2 = urllib.request.Request(loc, headers={"User-Agent": "APP/com.xiaomi.mihome"})
    with opener.open(req2, timeout=30) as r:
        r.read()
    service_token = None
    for c in cj:
        if c.name == "serviceToken":
            service_token = c.value
    if not service_token:
        raise Exception("no serviceToken (passToken 可能已过期)")
    return resp["ssecurity"], service_token


def sign(uri, data, ssecurity):
    if not isinstance(data, str):
        data = json.dumps(data)
    nonce = b64encode(urandom(8) + int(time.time() / 60).to_bytes(4, "big")).decode()
    h = sha256()
    h.update(b64decode(ssecurity))
    h.update(b64decode(nonce))
    snonce = b64encode(h.digest()).decode()
    msg = "&".join([uri, snonce, nonce, "data=" + data])
    sig = hmac_new(key=b64decode(snonce), msg=msg.encode(), digestmod=sha256).digest()
    return {"_nonce": nonce, "data": data, "signature": b64encode(sig).decode()}


def main():
    with open(sys.argv[1]) as f:
        tokens = json.load(f)
    ssecurity, service_token = service_login(tokens)
    uri = "/home/device_list"
    signed = sign(uri, {"getVirtualModel": False, "getHuamiDevices": 0}, ssecurity)
    body = urllib.parse.urlencode(signed).encode()
    cookie_hdr = "; ".join([
        f"userId={tokens['userId']}",
        f"serviceToken={service_token}",
        f"PassportDeviceId={tokens['deviceId']}",
    ])
    req = urllib.request.Request(
        "https://api.io.mi.com/app" + uri,
        data=body,
        headers={
            "User-Agent": UA_API,
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            "Cookie": cookie_hdr,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read().decode("utf-8", "replace"))
    result = resp.get("result", resp)
    if isinstance(result, dict) and "wait_second" in result:
        print("THROTTLED:", json.dumps(result))
        return
    devs = result.get("list", [])
    print(f"OK: {len(devs)} devices")
    for d in devs[:8]:
        print(" -", d.get("name"), "|", d.get("model"), "| online:", d.get("isOnline"))


if __name__ == "__main__":
    main()
