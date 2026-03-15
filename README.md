# VPS 流量查询 (AstrBot)

从 `nonebot_plugin_vpstraffic` 迁移到 AstrBot 的版本。

## 功能

- `/vps` 查询 VPS 网卡流量（管理员权限）
- 每月重置流量基线
- 每 5 分钟更新 Clash `subscription_userinfo.json`

## 指令

- `/vps` 查询流量统计

## 数据文件

- 流量基线：`data/plugin_data/vpstraffic/vps_traffic.json`
- Clash 订阅信息：`data/plugin_data/vpstraffic/subscription_userinfo.json`

## 配置项（可视化配置）

- `net_iface`：网卡名（必填）
- `net_server`：远程主机 IP（为空则读取本机）
- `net_reset_day`：每月重置日（默认 6）
- `ssh_user`：SSH 用户名（默认 `root`）
- `ssh_port`：SSH 端口（默认 22）
- `ssh_key_path`：SSH 私钥路径（默认 `data/id_rsa`）
- `total_gb`：订阅总流量（GB，默认 1024）

## 说明

- 远程读取依赖 SSH 私钥登录，请确保 `ssh_key_path` 可读取。
- 没有配置 `net_server` 时，插件会读取本机网卡流量（`psutil`）。

## 依赖

- `paramiko`
- `psutil`
