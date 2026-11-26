#!/usr/bin/env python3
"""小米米家控制包装器 - 让 Muse 直接调用 MCP 工具"""
import asyncio
import sys
sys.path.insert(0, "/home/hatch/workspace/xiaomi-mcp")

import mcp_server


async def main():
    if len(sys.argv) < 2:
        print("用法: mi.py <工具名> [参数...]")
        print("工具: get_devices, get_device_status, control_device, play_music,")
        print("      pause_music, resume_music, set_volume, tts, xiaoai_command, get_speaker_status")
        return

    tool = sys.argv[1]
    args = sys.argv[2:]

    fn = getattr(mcp_server, tool, None)
    if not fn:
        print(f"未知工具: {tool}")
        return

    # 根据工具名映射参数
    if tool == "get_devices":
        result = await fn()
    elif tool == "get_device_status":
        result = await fn(args[0] if args else "")
    elif tool == "control_device":
        result = await fn(args[0], args[1], args[2] if len(args) > 2 else "")
    elif tool == "play_music":
        result = await fn(args[0] if args else "")
    elif tool == "set_volume":
        result = await fn(int(args[0]))
    elif tool == "tts":
        result = await fn(" ".join(args))
    elif tool == "xiaoai_command":
        result = await fn(" ".join(args))
    else:
        result = await fn()

    print(result)


if __name__ == "__main__":
    asyncio.run(main())
