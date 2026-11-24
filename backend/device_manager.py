"""iOS device manager powered by pymobiledevice3 v11+ (supports iOS 17-27+ and legacy iOS <17)."""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from packaging.version import Version

from pymobiledevice3 import usbmux
from pymobiledevice3.exceptions import (
    AlreadyMountedError,
    PyMobileDevice3Exception,
    RemotePairingCompletedError,
    UserspaceTunnelUnavailableError,
)
from pymobiledevice3.lockdown import LockdownClient, create_using_usbmux
from pymobiledevice3.pair_records import iter_remote_paired_identifiers
from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
from pymobiledevice3.remote.tunnel_service import RemotePairingLockdownService
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
from pymobiledevice3.services.amfi import AmfiService
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.services.mobile_image_mounter import (
    PersonalizedImageMounter,
    auto_mount,
)
from pymobiledevice3.services.simulate_location import DtSimulateLocation
from pymobiledevice3.tunneld.api import get_tunneld_devices


LogCallback = Callable[[dict[str, Any]], Awaitable[None]]
DeviceStateCallback = Callable[[dict[str, Any]], Awaitable[None]]


class DeviceManager:
    """Manages iOS connection, DDI mounting, and DVT/lockdown location channels."""

    def __init__(
        self,
        on_state_change: Optional[DeviceStateCallback] = None,
        on_log: Optional[LogCallback] = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self._on_state_change = on_state_change
        self._on_log = on_log

        self._tunnel: Optional[UserspaceRsdTunnel] = None
        self._rsd: Optional[RemoteServiceDiscoveryService] = None
        self._lockdown: Optional[LockdownClient] = None
        self._dvt: Optional[DvtProvider] = None
        self._loc_sim: Optional[LocationSimulation] = None
        self._legacy_loc: Optional[DtSimulateLocation] = None

        self.connected: bool = False
        self.transport: str = ""  # "usb" | "wifi" | ""
        self.ddi_mounted: bool = False
        self.status_message: str = "未连接设备"
        self.device_info: dict[str, Any] = {}
        self.logs: deque[dict[str, Any]] = deque(maxlen=150)

    def get_state(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "transport": self.transport,
            "ddi_mounted": self.ddi_mounted,
            "status_message": self.status_message,
            "device_info": dict(self.device_info),
        }

    def get_logs(self) -> list[dict[str, Any]]:
        return list(self.logs)

    async def add_log(self, level: str, message: str) -> None:
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        self.logs.append(entry)
        if self._on_log is not None:
            with contextlib.suppress(Exception):
                await self._on_log(entry)

    async def _emit_state(self) -> None:
        if self._on_state_change is not None:
            with contextlib.suppress(Exception):
                await self._on_state_change(self.get_state())

    async def _read_lockdown_summary(self, lockdown: LockdownClient) -> dict[str, Any]:
        dev_mode: Optional[bool] = None
        if lockdown.has_developer_mode:
            with contextlib.suppress(Exception):
                dev_mode = await lockdown.get_developer_mode_status()
        name = lockdown.all_values.get("DeviceName", "iOS Device")
        product_type = lockdown.all_values.get("ProductType", "")
        return {
            "udid": lockdown.udid,
            "name": name,
            "product_type": product_type,
            "ios_version": lockdown.product_version,
            "build_version": lockdown.product_build_version or "",
            "developer_mode": dev_mode,
        }

    async def list_devices(self) -> dict[str, Any]:
        """List attached USB iOS devices and saved Wi-Fi RemotePairing records."""
        usb_devices: list[dict[str, Any]] = []
        raw_usb = await usbmux.list_devices()
        seen_serials: set[str] = set()

        for dev in raw_usb:
            if not dev.is_usb or dev.serial in seen_serials:
                continue
            seen_serials.add(dev.serial)

            # Reuse live info if this device is already connected to avoid disturbing the active tunnel.
            if self.connected and self.device_info.get("udid") == dev.serial:
                info = dict(self.device_info)
                info["paired"] = True
                info["connected"] = True
                usb_devices.append(info)
                continue

            entry: dict[str, Any] = {
                "udid": dev.serial,
                "name": f"iPhone/iPad ({dev.serial[:8]}...)",
                "product_type": "",
                "ios_version": "",
                "build_version": "",
                "developer_mode": None,
                "paired": False,
                "connected": False,
            }
            lockdown: Optional[LockdownClient] = None
            try:
                lockdown = await create_using_usbmux(serial=dev.serial, autopair=False)
                entry.update(await self._read_lockdown_summary(lockdown))
                entry["paired"] = True
            except Exception as exc:
                entry["error"] = str(exc)
            finally:
                if lockdown is not None:
                    with contextlib.suppress(Exception):
                        await lockdown.close()
            usb_devices.append(entry)

        wifi_records: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            for rec_id in sorted(set(iter_remote_paired_identifiers())):
                wifi_records.append(
                    {
                        "id": rec_id,
                        "connected": self.connected
                        and self.transport == "wifi"
                        and self.device_info.get("udid") == rec_id,
                    }
                )

        return {"usb": usb_devices, "wifi": wifi_records}

    async def _cleanup_handles(self) -> None:
        if self._loc_sim is not None:
            with contextlib.suppress(Exception):
                await self._loc_sim.__aexit__(None, None, None)
            self._loc_sim = None
        if self._dvt is not None:
            with contextlib.suppress(Exception):
                await self._dvt.__aexit__(None, None, None)
            self._dvt = None
        self._legacy_loc = None

        if self._tunnel is not None:
            with contextlib.suppress(Exception):
                await self._tunnel.aclose()
            self._tunnel = None
            self._rsd = None
        elif self._rsd is not None:
            with contextlib.suppress(Exception):
                await self._rsd.close()
            self._rsd = None

        if self._lockdown is not None:
            with contextlib.suppress(Exception):
                await self._lockdown.close()
            self._lockdown = None

        self.connected = False
        self.transport = ""
        self.ddi_mounted = False

    async def _is_ddi_mounted_quick(self, provider: Any) -> bool:
        if isinstance(provider, RemoteServiceDiscoveryService):
            with contextlib.suppress(Exception):
                async with PersonalizedImageMounter(lockdown=provider) as mounter:
                    return await mounter.is_image_mounted(PersonalizedImageMounter.IMAGE_TYPE)
        return False

    async def _mount_ddi(self, provider: Any) -> bool:
        await self.add_log("info", "正在检查/下载并挂载开发者镜像 (DDI / Cryptex1，首次需下载约17MB，请稍候)...")
        try:
            await auto_mount(provider)
            await self.add_log("info", "开发者镜像挂载成功")
            return True
        except AlreadyMountedError:
            await self.add_log("info", "开发者镜像已挂载，无需重复挂载")
            return True

    async def _connect_rsd_dvt(self, rsd: RemoteServiceDiscoveryService, auto_mount_ddi: bool) -> None:
        """Open DVT LocationSimulation quickly when already available; auto-mount DDI on demand."""
        self.ddi_mounted = await self._is_ddi_mounted_quick(rsd)
        try:
            await self._open_dvt_location(rsd)
            return
        except Exception as first_exc:
            if not auto_mount_ddi:
                raise first_exc
            await self.add_log("warn", f"DVT 通道尚未就绪 ({first_exc})，尝试自动挂载开发者镜像...")
            self.ddi_mounted = await self._mount_ddi(rsd)
            await self._open_dvt_location(rsd)

    async def mount_ddi_now(self) -> dict[str, Any]:
        """Explicitly mount DeveloperDiskImage / Cryptex1 on the currently connected device."""
        async with self._lock:
            if not self.connected:
                raise RuntimeError("请先连接设备后再挂载开发者镜像")
            provider: Any = self._rsd or self._lockdown
            if provider is None:
                raise RuntimeError("未找到可用的设备通道")
            self.ddi_mounted = await self._mount_ddi(provider)
            await self._emit_state()
            return self.get_state()

    async def connect(
        self,
        udid: Optional[str] = None,
        transport: str = "usb",
        auto_mount_ddi: bool = True,
    ) -> dict[str, Any]:
        """Connect to an iOS device (iOS 17-27+ via userspace RSD tunnel, or iOS <17 via lockdown)."""
        async with self._lock:
            await self._cleanup_handles()
            self.status_message = "正在连接设备..."
            await self._emit_state()

            try:
                if transport == "wifi":
                    await self.add_log("info", f"正在通过 Wi-Fi RemotePairing 连接设备 ({udid or '默认配对记录'})...")
                    tunnel = UserspaceRsdTunnel(serial=udid or None, remotepairing=True)
                    self._tunnel = tunnel
                    rsd = await tunnel.aopen()
                    self._rsd = rsd

                    self.device_info = {
                        "udid": rsd.udid,
                        "name": rsd.all_values.get("DeviceName", "iOS Device"),
                        "product_type": rsd.all_values.get("ProductType", ""),
                        "ios_version": rsd.product_version,
                        "build_version": rsd.product_build_version or "",
                        "developer_mode": True,
                    }
                    await self._connect_rsd_dvt(rsd, auto_mount_ddi=auto_mount_ddi)
                    self.connected = True
                    self.transport = "wifi"
                    self.status_message = f"已无线连接 {self.device_info['name']} (iOS {self.device_info['ios_version']})"
                    await self.add_log("info", self.status_message)
                    await self._emit_state()
                    return self.get_state()

                # USB transport: first probe device info & Developer Mode via short lockdown session
                await self.add_log("info", "正在通过 USB 读取设备信息与信任状态...")
                probe_lockdown = await create_using_usbmux(serial=udid or None, autopair=True)
                try:
                    info = await self._read_lockdown_summary(probe_lockdown)
                    resolved_udid = info["udid"]
                    ios_ver = info["ios_version"]
                finally:
                    with contextlib.suppress(Exception):
                        await probe_lockdown.close()

                self.device_info = info
                if info.get("developer_mode") is False:
                    raise RuntimeError(
                        "设备尚未开启【开发者模式】！请在 iPhone 上打开：设置 -> 隐私与安全性 -> 开发者模式（如未显示可点击本软件的【开发者开关】），开启并重启手机后再连接。"
                    )

                parsed_ver = Version(ios_ver)
                if parsed_ver >= Version("17.0"):
                    await self.add_log("info", f"检测到 iOS {ios_ver}，正在建立免管理员权限 Userspace RSD 隧道...")
                    rsd: Optional[RemoteServiceDiscoveryService] = None
                    try:
                        tunnel = UserspaceRsdTunnel(
                            serial=resolved_udid,
                            autopair=True,
                            remotepairing_fallback=False,
                        )
                        self._tunnel = tunnel
                        rsd = await tunnel.aopen()
                        self._rsd = rsd
                    except UserspaceTunnelUnavailableError:
                        # iOS 17.0 - 17.3.1 lacks CoreDeviceProxy over USB; try tunneld if running.
                        await self.add_log("warn", f"iOS {ios_ver} 不支持免权限 USB Userspace 隧道，尝试连接 tunneld / RemotePairing...")
                        self._tunnel = None
                        tunneld_rsds = []
                        with contextlib.suppress(Exception):
                            tunneld_rsds = await get_tunneld_devices()
                        matched = next((r for r in tunneld_rsds if r.udid == resolved_udid), None)
                        if matched is not None:
                            for other in tunneld_rsds:
                                if other is not matched:
                                    with contextlib.suppress(Exception):
                                        await other.close()
                            rsd = matched
                            self._rsd = rsd
                        else:
                            try:
                                tunnel = UserspaceRsdTunnel(
                                    serial=resolved_udid,
                                    autopair=True,
                                    remotepairing_fallback=True,
                                )
                                self._tunnel = tunnel
                                rsd = await tunnel.aopen()
                                self._rsd = rsd
                            except Exception as rp_exc:
                                raise RuntimeError(
                                    f"iOS {ios_ver}（17.0-17.3）需要以管理员身份运行 `pymobiledevice3 remote tunneld` 或先点击【无线配对】并保持同局域网 Wi-Fi。详情: {rp_exc}"
                                ) from rp_exc

                    await self._connect_rsd_dvt(rsd, auto_mount_ddi=auto_mount_ddi)
                else:
                    await self.add_log("info", f"检测到 iOS {ios_ver} (<17)，正在连接传统模拟定位服务...")
                    lockdown = await create_using_usbmux(serial=resolved_udid, autopair=True)
                    self._lockdown = lockdown
                    if auto_mount_ddi:
                        self.ddi_mounted = await self._mount_ddi(lockdown)
                    self._legacy_loc = DtSimulateLocation(lockdown)

                self.connected = True
                self.transport = "usb"
                self.status_message = f"已连接 {info['name']} (iOS {ios_ver})"
                await self.add_log("info", self.status_message)
                await self._emit_state()
                return self.get_state()

            except Exception as exc:
                await self._cleanup_handles()
                self.status_message = f"连接失败: {exc}"
                await self.add_log("error", self.status_message)
                await self._emit_state()
                raise

    async def _open_dvt_location(self, rsd: RemoteServiceDiscoveryService) -> None:
        await self.add_log("info", "正在打开 DVT LocationSimulation 通道...")
        dvt = DvtProvider(rsd)
        await dvt.__aenter__()
        self._dvt = dvt
        loc_sim = LocationSimulation(dvt)
        await loc_sim.__aenter__()
        self._loc_sim = loc_sim
        await self.add_log("info", "DVT 虚拟定位通道已就绪")

    async def set_location(self, lat: float, lng: float) -> None:
        """Push a WGS-84 coordinate to the connected iOS device."""
        async with self._lock:
            if not self.connected:
                raise RuntimeError("设备未连接，请先点击【连接设备】")
            if self._loc_sim is not None:
                await self._loc_sim.set(float(lat), float(lng))
            elif self._legacy_loc is not None:
                await self._legacy_loc.set(float(lat), float(lng))
            else:
                raise RuntimeError("虚拟定位通道未初始化")

    async def clear_location(self) -> None:
        """Restore the connected iOS device's real GPS location."""
        async with self._lock:
            if not self.connected:
                raise RuntimeError("设备未连接，请先连接设备后再恢复定位")
            if self._loc_sim is not None:
                await self._loc_sim.clear()
            elif self._legacy_loc is not None:
                await self._legacy_loc.clear()
            else:
                raise RuntimeError("虚拟定位通道未初始化")
            await self.add_log("info", "已向 iPhone 发送恢复真实定位指令")

    async def disconnect(self, clear_location: bool = False) -> dict[str, Any]:
        async with self._lock:
            if clear_location and self.connected:
                with contextlib.suppress(Exception):
                    if self._loc_sim is not None:
                        await self._loc_sim.clear()
                    elif self._legacy_loc is not None:
                        await self._legacy_loc.clear()
            await self._cleanup_handles()
            self.device_info = {}
            self.status_message = "已断开连接"
            await self.add_log("info", "已断开设备连接")
            await self._emit_state()
            return self.get_state()

    async def reveal_developer_mode(self, udid: Optional[str] = None) -> str:
        """Reveal the Developer Mode toggle in iOS Settings -> Privacy & Security."""
        if self.connected and self.device_info.get("developer_mode") is True:
            msg = "当前设备已开启开发者模式，无需再显示开关"
            await self.add_log("info", msg)
            return msg

        lockdown = await create_using_usbmux(serial=udid or None, autopair=True)
        try:
            await AmfiService(lockdown).reveal_developer_mode_option_in_ui()
            msg = "已向 iPhone 发送指令：请在【设置 -> 隐私与安全性 -> 开发者模式】中打开开关并按提示重启手机。"
            await self.add_log("info", msg)
            return msg
        finally:
            with contextlib.suppress(Exception):
                await lockdown.close()

    async def pair_wifi(self, udid: Optional[str] = None) -> dict[str, Any]:
        """Bootstrap promptless RemotePairing record over USB for future Wi-Fi connection."""
        lockdown = await create_using_usbmux(serial=udid or None, autopair=True)
        service = None
        try:
            service = await RemotePairingLockdownService.create(lockdown)
            try:
                await service.connect(autopair=True)
            except RemotePairingCompletedError:
                await service.close()
                service = await RemotePairingLockdownService.create(lockdown)
                await service.connect(autopair=False)

            info = service.handshake_info or {}
            msg = f"无线 Wi-Fi 配对成功 ({lockdown.udid})！下次同局域网下可直接通过 Wi-Fi 连接。"
            await self.add_log("info", msg)
            return {"ok": True, "udid": lockdown.udid, "message": msg, "handshake_keys": list(info.keys())}
        except PyMobileDevice3Exception as exc:
            raise RuntimeError(f"无线配对失败（仅 iOS 17+ 支持 RemotePairing）: {exc}") from exc
        finally:
            if service is not None:
                with contextlib.suppress(Exception):
                    await service.close()
            with contextlib.suppress(Exception):
                await lockdown.close()
