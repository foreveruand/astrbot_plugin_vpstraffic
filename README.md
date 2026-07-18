# Xray 用户流量查询 (AstrBot)

按 Xray 用户 `email` 统计当期上行、下行和合计流量。

## 功能

- 管理员使用 `/vps` 查看各用户的流量使用情况。
- AstrBot 定时任务每 5 分钟读取一次 Xray 用户流量并累计。
- 每月重置日的 00:00 清零本地账本和 Xray 用户计数器。
- 通过 SSH 在 Xray 服务器执行 `xray api statsquery`，因此 Xray API 可以仅监听 `127.0.0.1`。

## Xray 配置

除用户策略外，Xray 还必须启用统计对象和 `StatsService`。旧式 API 入站配置还需要将 `api` 入站路由到同名 API 出站：

```json
{
  "stats": {},
  "api": {
    "tag": "api",
    "services": ["StatsService"]
  },
  "routing": {
    "rules": [
      {
        "inboundTag": ["api"],
        "outboundTag": "api"
      }
    ]
  }
}
```

每个需要统计的 Xray 用户都必须设置非空 `email`。插件读取的计数器名称为 `user>>>[email]>>>traffic>>>uplink` 和 `user>>>[email]>>>traffic>>>downlink`。

## 插件配置

- `xray_ssh_host`：Xray 所在 SSH 主机，默认 `dm`。
- `xray_ssh_user`：SSH 用户名；留空时使用 SSH 配置或当前系统用户。
- `xray_ssh_port`：SSH 端口，默认 `22`。
- `xray_api_address`：从 SSH 主机访问的 API 地址，默认 `127.0.0.1:10085`。
- `xray_command`：SSH 主机上的 Xray 命令或可执行文件路径，默认 `xray`。
- `net_reset_day`：每月清零日，支持 `1` 至 `28`，默认 `6`。

插件所在服务器必须能无交互执行 `ssh dm`。不需要在 AstrBot 服务器安装 Xray、Xray API 的 Python 包或 protobuf 文件。

首次启用时，插件会以当前 Xray 计数器建立基线，因此不会回溯统计启用前的流量，也不会清零现有计数器。

## 数据文件

流量账本保存在 `data/plugin_data/astrbot_plugin_vpstraffic/xray_user_traffic.json`。
