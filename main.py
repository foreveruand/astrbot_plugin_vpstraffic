import asyncio
import json
import calendar
from datetime import date, datetime
from pathlib import Path
from typing import Any

import paramiko
import psutil

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils import path_utils


class VPSTrafficPlugin(Star):
    """VPS traffic query plugin migrated from nonebot.

    Command:
    - /vps
    """

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        config = config or {}

        self.net_iface = str(config.get("net_iface", ""))
        self.net_server = str(config.get("net_server", ""))
        self.net_reset_day = int(config.get("net_reset_day", 6))
        self.ssh_user = str(config.get("ssh_user", "root"))
        self.ssh_port = int(config.get("ssh_port", 22))
        self.total_gb = float(config.get("total_gb", 1024))

        data_dir = path_utils.get_data_dir()
        plugin_data_dir = path_utils.get_plugin_data_dir("vpstraffic")
        plugin_data_dir.mkdir(parents=True, exist_ok=True)

        self.ssh_key_path = Path(
            config.get("ssh_key_path", str(data_dir / "id_rsa"))
        )
        self.data_file = plugin_data_dir / "vps_traffic.json"
        self.sub_info_file = plugin_data_dir / "subscription_userinfo.json"

        self._reset_job_name = "vpstraffic_reset"
        self._clash_job_name = "vpstraffic_clash_update"

    async def initialize(self) -> None:
        await self._ensure_cron_jobs()

    async def _ensure_cron_jobs(self) -> None:
        cron = getattr(self.context, "cron_manager", None)
        if cron is None:
            return

        existing = await cron.list_jobs("basic")
        for job in existing:
            if job.name in {self._reset_job_name, self._clash_job_name}:
                await cron.delete_job(job.job_id)

        reset_expression = f"59 23 {self.net_reset_day} * *"
        await cron.add_basic_job(
            name=self._reset_job_name,
            cron_expression=reset_expression,
            handler=self._reset_job,
            description="Reset VPS traffic baseline",
            enabled=True,
            persistent=False,
        )

        await cron.add_basic_job(
            name=self._clash_job_name,
            cron_expression="*/5 * * * *",
            handler=self._update_clash_userinfo,
            description="Update Clash subscription userinfo",
            enabled=True,
            persistent=False,
        )

    def _load_data(self) -> dict[str, Any]:
        if self.data_file.exists():
            try:
                return json.loads(self.data_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"base_rx": 0.0, "base_tx": 0.0, "last_month": None}

    def _save_data(self, data: dict[str, Any]) -> None:
        self.data_file.write_text(json.dumps(data), encoding="utf-8")

    def _safe_date(self, year: int, month: int, day: int) -> date:
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(day, last_day))

    def _get_period_string(self, today: date | None = None) -> str:
        if today is None:
            today = datetime.now().date()

        if today.day < self.net_reset_day:
            if today.month == 1:
                start_year, start_month = today.year - 1, 12
            else:
                start_year, start_month = today.year, today.month - 1
            start = self._safe_date(start_year, start_month, self.net_reset_day)
            end = today
        else:
            start = date(today.year, today.month, self.net_reset_day)
            end = today
        return f"{start.strftime('%Y-%m-%d')} 至 {end.strftime('%Y-%m-%d')}"

    def _get_local_traffic(self) -> tuple[float, float]:
        stats = psutil.net_io_counters(pernic=True)
        if self.net_iface not in stats:
            raise RuntimeError(f"网卡 {self.net_iface} 不存在，请修改为实际网卡名")
        nic = stats[self.net_iface]
        rx_gb = nic.bytes_recv / 1024 / 1024 / 1024
        tx_gb = nic.bytes_sent / 1024 / 1024 / 1024
        return rx_gb, tx_gb

    def _get_remote_traffic_sync(self) -> tuple[float, float]:
        if not self.net_server:
            return self._get_local_traffic()

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=self.net_server,
            port=self.ssh_port,
            username=self.ssh_user,
            key_filename=str(self.ssh_key_path),
        )
        cmd = f"cat /proc/net/dev | grep {self.net_iface}"
        stdin, stdout, stderr = ssh.exec_command(cmd)
        line = stdout.read().decode().strip()
        ssh.close()

        if not line:
            raise RuntimeError(f"未能读取网卡 {self.net_iface} 的流量信息")

        parts = line.split()
        rx_bytes = float(parts[1])
        tx_bytes = float(parts[9])
        rx_gb = rx_bytes / 1024 / 1024 / 1024
        tx_gb = tx_bytes / 1024 / 1024 / 1024
        return rx_gb, tx_gb

    async def _get_remote_traffic(self) -> tuple[float, float]:
        return await asyncio.to_thread(self._get_remote_traffic_sync)

    async def _reset_job(self) -> None:
        rx, tx = await self._get_remote_traffic()
        self._save_data({"base_rx": rx, "base_tx": tx})

    async def _update_clash_userinfo(self) -> None:
        try:
            rx_gb, tx_gb = await self._get_remote_traffic()
            data = self._load_data()

            used_rx_gb = max(0.0, rx_gb - float(data.get("base_rx", 0.0)))
            used_tx_gb = max(0.0, tx_gb - float(data.get("base_tx", 0.0)))

            download = int(used_rx_gb * 1024**3)
            upload = int(used_tx_gb * 1024**3)

            today = datetime.now().date()
            if today.day < self.net_reset_day:
                year = today.year
                month = today.month
            else:
                if today.month == 12:
                    year, month = today.year + 1, 1
                else:
                    year, month = today.year, today.month + 1

            expire_date = self._safe_date(year, month, self.net_reset_day)
            expire_ts = int(
                datetime.combine(expire_date, datetime.max.time()).timestamp()
            )

            info = {
                "download": download,
                "upload": upload,
                "total": int(self.total_gb * 1024**3),
                "expire": expire_ts,
            }
            self.sub_info_file.write_text(json.dumps(info), encoding="utf-8")
        except Exception:
            return
        
    @filter.command("vps")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def vps_command(self, event: AstrMessageEvent):
        try:
            data = self._load_data()
            rx, tx = await self._get_remote_traffic()

            used_rx = rx - float(data.get("base_rx", 0.0))
            used_tx = tx - float(data.get("base_tx", 0.0))
            total = used_rx + used_tx

            period = self._get_period_string(datetime.now().date())

            msg = (
                "VPS 流量统计\n"
                f"下行: {used_rx:.2f} GB\n"
                f"上行: {used_tx:.2f} GB\n"
                f"总流量: {total:.2f} GB\n"
                f"统计周期: {period}"
            )
        except Exception as e:
            msg = f"获取流量统计失败：{e}"

        yield event.plain_result(msg)
