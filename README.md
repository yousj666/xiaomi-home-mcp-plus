# xiaomi-home-mcp-plus

米家（Xiaomi Home）全功能控制工具集：在 MCP Server 的基础上，按 **MIoT-Spec 自动发现每台设备的全部属性与动作**，实现「有什么设备就能控什么」，并附带一键家庭状态报告。

## 功能

- **MCP Server**（`mcp_server.py`）：设备列表、状态查询、开关/亮度/色温控制、小爱音箱播放/TTS/语音指令等工具，可直接接入支持 MCP 的 AI 客户端。
- **通用 MIoT 读写**（`mix.py` + `mi_ext.py` + `miot_spec.py`）：
  - 自动从 [miot-spec.org](https://miot-spec.org) 拉取并缓存每个型号的 spec（属性/动作定义）
  - `get` 读全部可读属性，`set` 按 spec 校验类型与可写性后写属性并回读确认，`do` 调用任意动作
  - 冰箱（各舱室实际/目标温度、模式）、空气炸锅（状态、目标温度/时长、开始/取消/暂停）等友好命令
  - 摄像头清单与关键状态（开关、夜视、录像模式、存储卡容量/剩余录像时长等）
  - 摄像头云端能力探测（账号消息事件、云存端点、流配置动作）
- **家庭状态报告**（`mix.py report`）：跨设备批量读取全部状态，按房间分组生成 Markdown 报告。
- **安全护栏**：门锁类设备强制只读；写操作均有 spec 校验与回读确认；设备清单本地缓存以避开云端节流。

## 快速开始

```bash
python3 -m venv venv && source venv/bin/activate
pip install fastmcp aiohttp

# 1) 登录（扫码），凭证保存到 ~/.xiaomi-mcp/tokens.json（权限 600）
python login.py

# 2) 命令行使用
python mi.py get_devices              # 基础工具
python mix.py devices                 # 全量设备清单
python mix.py spec 冰箱               # 查看某设备的全部属性/动作
python mix.py get 冰箱                # 读全部可读属性
python mix.py fridge                  # 冰箱状态
python mix.py fridge set 冷藏 4       # 设置冷藏目标温度
python mix.py airfryer                # 空气炸锅状态
python mix.py cameras                 # 摄像头清单与状态
python mix.py report                  # 生成家庭状态报告（reports/ 目录）

# 3) 作为 MCP Server 运行
python mcp_server.py
```

也可以用环境变量提供凭证（见 `.env.example`）：`XIAOMI_PASS_TOKEN`、`XIAOMI_USER_ID`、`XIAOMI_DEVICE_ID`。

## 已知限制

- 小米云端对 API 调用有节流（返回 `wait_second`），工具内置缓存与节流提示，请勿高频猛刷。
- 小米摄像头的直播/回放走私有 P2P 协议，云端 API 不提供视频流地址；本工具能读摄像头状态与存储信息、探测云端事件/云存接口，但不提供画面拉流。
- BLE/网关子设备经云端可读性视型号而定。

## 免责声明

非官方项目，与小米无关。使用云端 API 存在账号风控可能，请自行评估。凭证文件请妥善保管，切勿提交到任何仓库。

## License

MIT
