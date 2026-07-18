import asyncio
import json
import shlex
from datetime import date, datetime
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path


class VPSTrafficPlugin(Star):
    """Collect and report Xray per-user traffic through its local API."""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        self.context = context
        self.config = config
        self.xray_ssh_host = str(config.get("xray_ssh_host", "dm"))
        self.xray_ssh_user = str(config.get("xray_ssh_user", ""))
        self.xray_ssh_port = int(config.get("xray_ssh_port", 22))
        self.xray_api_address = str(config.get("xray_api_address", "127.0.0.1:10085"))
        self.xray_command = str(config.get("xray_command", "xray"))
        self.traffic_collect_interval = min(
            max(int(config.get("traffic_collect_interval", 5)), 1), 59
        )
        self.net_reset_day = min(max(int(config.get("net_reset_day", 6)), 1), 28)

        plugin_data_dir = (
            Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_vpstraffic"
        )
        plugin_data_dir.mkdir(parents=True, exist_ok=True)
        self.data_file = plugin_data_dir / "xray_user_traffic.json"
        self._collect_job_name = "vpstraffic_xray_collect"
        self._reset_job_name = "vpstraffic_reset"
        self._traffic_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Register traffic collection and monthly reset jobs."""
        await self._ensure_cron_jobs()

    async def _ensure_cron_jobs(self) -> None:
        """Replace this plugin's non-persistent scheduled jobs."""
        cron = self.context.cron_manager
        existing = await cron.list_jobs("basic")
        job_names = {
            self._collect_job_name,
            self._reset_job_name,
            "vpstraffic_clash_update",
        }
        for job in existing:
            if job.name in job_names:
                await cron.delete_job(job.job_id)

        await cron.add_basic_job(
            name=self._collect_job_name,
            cron_expression=f"*/{self.traffic_collect_interval} * * * *",
            handler=self._collect_traffic,
            description="Collect Xray per-user traffic",
            enabled=True,
            persistent=False,
        )
        await cron.add_basic_job(
            name=self._reset_job_name,
            cron_expression=f"0 0 {self.net_reset_day} * *",
            handler=self._reset_traffic,
            description="Reset Xray per-user traffic",
            enabled=True,
            persistent=False,
        )

    def _load_data(self) -> dict[str, Any]:
        """Load the current accounting period and per-email usage ledger."""
        if self.data_file.exists():
            try:
                data = json.loads(self.data_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("users"), dict):
                    return data
            except (OSError, json.JSONDecodeError):
                logger.warning(
                    "Failed to read Xray traffic data from %s", self.data_file
                )
        return {"period_start": "", "users": {}, "last_counters": {}}

    def _save_data(self, data: dict[str, Any]) -> None:
        """Persist the current per-user traffic ledger."""
        self.data_file.write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    def _period_start(self, today: date | None = None) -> date:
        """Return the first day of the billing period containing today."""
        today = today or datetime.now().date()
        if today.day >= self.net_reset_day:
            return date(today.year, today.month, self.net_reset_day)
        if today.month == 1:
            return date(today.year - 1, 12, self.net_reset_day)
        return date(today.year, today.month - 1, self.net_reset_day)

    def _period_string(self, today: date | None = None) -> str:
        """Format the current accounting period for the command response."""
        today = today or datetime.now().date()
        start = self._period_start(today)
        return f"{start:%Y-%m-%d} 至 {today:%Y-%m-%d}"

    async def _query_xray_stats(self, *, reset: bool) -> dict[str, dict[str, int]]:
        """Query Xray user counters and optionally clear Xray's counters.

        Args:
            reset: Whether Xray should reset counters after returning their values.

        Returns:
            Traffic bytes grouped by email and direction.

        Raises:
            RuntimeError: If SSH, the Xray command, or its JSON response fails.
        """
        remote_args = [
            self.xray_command,
            "api",
            "statsquery",
            f"--server={self.xray_api_address}",
            "--pattern",
            "user>>>",
        ]
        if reset:
            remote_args.append("--reset")

        if self.xray_ssh_host:
            destination = self.xray_ssh_host
            if self.xray_ssh_user:
                destination = f"{self.xray_ssh_user}@{destination}"
            command = " ".join(shlex.quote(arg) for arg in remote_args)
            args = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
            if self.xray_ssh_port:
                args.extend(["-p", str(self.xray_ssh_port)])
            args.extend([destination, command])
        else:
            args = remote_args

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("Xray traffic query timed out") from None

        if process.returncode:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or "Xray traffic query failed")

        try:
            response = json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Xray returned invalid statistics data") from exc

        users: dict[str, dict[str, int]] = {}
        for stat in response.get("stat", []):
            name = stat.get("name", "")
            parts = name.split(">>>")
            if len(parts) != 4 or parts[0] != "user" or parts[2] != "traffic":
                continue
            email, direction = parts[1], parts[3]
            if not email or direction not in {"uplink", "downlink"}:
                continue
            users.setdefault(email, {"uplink": 0, "downlink": 0})[direction] = int(
                stat.get("value", 0)
            )
        return users

    async def _collect_traffic(self) -> None:
        """Collect and clear Xray counters into the current local ledger."""
        async with self._traffic_lock:
            period_start = self._period_start().isoformat()
            data = self._load_data()
            if data.get("period_start") != period_start:
                if data.get("period_start"):
                    await self._query_xray_stats(reset=True)
                    users: dict[str, dict[str, int]] = {}
                else:
                    users = await self._query_xray_stats(reset=True)
                self._save_data(
                    {
                        "period_start": period_start,
                        "users": users,
                    }
                )
                return

            counters = await self._query_xray_stats(reset=True)
            users = data["users"]
            for email, traffic in counters.items():
                usage = users.setdefault(email, {"uplink": 0, "downlink": 0})
                usage["uplink"] += traffic["uplink"]
                usage["downlink"] += traffic["downlink"]
            self._save_data(data)

    async def _reset_traffic(self) -> None:
        """Clear the local ledger and Xray counters at the start of a new period."""
        async with self._traffic_lock:
            await self._query_xray_stats(reset=True)
            self._save_data(
                {
                    "period_start": self._period_start().isoformat(),
                    "users": {},
                }
            )

    @filter.command("vps")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def vps_command(self, event: AstrMessageEvent):
        """Show the current period's Xray traffic grouped by user email."""
        try:
            await self._collect_traffic()
            data = self._load_data()
            users = data["users"]
            lines = ["Xray 用户流量统计", f"统计周期: {self._period_string()}"]
            if not users:
                lines.append("暂无已记录的用户流量")
            else:
                ordered_users = sorted(
                    users.items(),
                    key=lambda item: (
                        int(item[1].get("uplink", 0)) + int(item[1].get("downlink", 0))
                    ),
                    reverse=True,
                )
                for email, usage in ordered_users:
                    uplink = int(usage.get("uplink", 0))
                    downlink = int(usage.get("downlink", 0))
                    total = uplink + downlink
                    lines.append(
                        f"{email}\n"
                        f"  下行: {downlink / 1024**3:.2f} GB | "
                        f"上行: {uplink / 1024**3:.2f} GB | "
                        f"合计: {total / 1024**3:.2f} GB"
                    )
            msg = "\n".join(lines)
        except Exception as exc:
            logger.warning("Failed to collect Xray traffic: %s", exc)
            msg = f"获取 Xray 用户流量失败：{exc}"

        yield event.plain_result(msg)
