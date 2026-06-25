"""万家乐设备抽象与控制 API 层。

该层将 protocol.py 提供的基础 TCP/HTTP 能力包装成"设备对象"：
  - WanjialeDevice：基类，描述任意设备；
  - WanjialeWaterHeater：热水器子类，封装开关机/设置温度/模式；

控制命令格式（基于前端 index.js 分析）：
  client.opt(deviceId, dvid, value)
  JSON: {"to":"did","cmd":"opt","mid":"xxx","as":{"dvid":"value"}}

dvid 含义：
  "1"  - 操作类型标识
  "2"  - 操作值（32位整数，编码：mode*16777216 + temp*65536 + other）
  "4"  - 开关机状态（0=关机，1=开机）
  "24" - 模式（4=舒适浴，5=随温感，10=ECO，11=SUR，14=厨房洗）
  "28" - 目标温度
  "251" - 杀菌状态

值编码方式：
  value = mode * 16777216 + temp * 65536 + byte2 * 256 + byte1
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Type

from .protocol import LOCAL_PORT, WanjialeProtocol

_LOGGER = logging.getLogger(__name__)

# ======================================================================
# 设备类型注册表
# ======================================================================
_DEVICE_TYPE_REGISTRY: Dict[str, Type["WanjialeDevice"]] = {}


def register_device_type(device_type: str) -> Callable[[Type["WanjialeDevice"]], Type["WanjialeDevice"]]:
    """装饰器：注册设备类型到注册表。"""

    def _decorator(cls: Type["WanjialeDevice"]) -> Type["WanjialeDevice"]:
        _DEVICE_TYPE_REGISTRY[device_type] = cls
        return cls

    return _decorator


def _resolve_device_class(raw_device: Dict[str, Any]) -> Type["WanjialeDevice"]:
    """FW3/BA5/DW3 专用设备识别。"""
    model = str(raw_device.get("model") or "").strip().lower()
    name = str(raw_device.get("name") or "").strip().lower()
    product = str(raw_device.get("product") or "").strip().lower()

    text = f"{name} {model} {product}"
    fw3_tokens = ("fw3", "ba5", "dw3", "电热水器", "热水器")
    if any(token in text for token in fw3_tokens):
        return WanjialeWaterHeater

    # FW3/BA5/DW3 的 as 中已确认包含 102=设置温度，101 疑似电源。
    # 若账号里只有这台设备，命中 101/102 即按热水器处理。
    as_data = raw_device.get("as", {})
    if isinstance(as_data, dict):
        fw3_dvids = {"101", "102"}
        if fw3_dvids & set(map(str, as_data.keys())):
            return WanjialeWaterHeater

    return WanjialeDevice


# ======================================================================
# 基类：WanjialeDevice
# ======================================================================
class WanjialeDevice:
    """任意万家乐设备的基类。"""

    platform = "sensor"
    category_cn = "通用设备"

    def __init__(
        self,
        protocol: WanjialeProtocol,
        raw_device: Dict[str, Any],
    ) -> None:
        self._protocol = protocol
        self._raw = raw_device

        self.did: str = str(raw_device.get("did") or "")
        self.name: str = str(raw_device.get("name") or self.did)
        self.model: str = str(raw_device.get("model") or "")
        self.online: bool = bool(raw_device.get("online"))
        self.product: str = str(raw_device.get("product") or "")
        self.firm: str = str(raw_device.get("firm") or "")

        # 局域网控制参数
        self.local_host: Optional[str] = raw_device.get("lanIp")
        self.local_port: int = raw_device.get("lanPort", 0)
        self.lan_pin: str = raw_device.get("lanPin", "")

        # 状态缓存
        self.attributes: Dict[str, Any] = dict(raw_device)

        # 最后确认在线时间（云端或局域网查询成功时更新）
        self._last_seen_online: float = time.time() if self.online else 0.0

    def refresh(self) -> None:
        """刷新设备状态。"""
        self.attributes.update(self._raw)

    def unique_id(self) -> str:
        return f"wanjiale-{self.did}"

    def is_lan_available(self) -> bool:
        return (
            self.local_host is not None
            and self.local_port > 0
            and len(self.lan_pin) > 0
        )

    # ------------------------------------------------------------------
    # 控制命令
    # ------------------------------------------------------------------
    def _send_opt(self, dvid: str, value: int) -> Dict[str, Any]:
        as_dict = {dvid: str(value)}
        if self.is_lan_available():
            return self._send_lan_control(as_dict)
        return self._send_cloud_control(as_dict)

    def _send_opt_pair(self, op_type: int, value: int) -> Dict[str, Any]:
        as_dict = {"1": str(op_type), "2": str(value)}
        if self.is_lan_available():
            return self._send_lan_control(as_dict)
        return self._send_cloud_control(as_dict)

    def _send_cloud_control(self, as_dict: Dict[str, Any]) -> Dict[str, Any]:
        def _connect_or_relogin() -> bool:
            if getattr(self._protocol, "_socket", None):
                return True
            try:
                return bool(self._protocol.connect_server())
            except Exception:
                _LOGGER.debug("建立长连接失败，尝试重新登录", exc_info=True)
            try:
                self._protocol.close_server()
                self._protocol.login()
                return bool(self._protocol.connect_server())
            except Exception:
                _LOGGER.debug("重新登录/建立长连接失败", exc_info=True)
                return False

        if not _connect_or_relogin():
            return {"error": "cloud unavailable"}

        try:
            result = self._protocol.send_control_async(self.did, as_dict)
            if isinstance(result, dict) and not result.get("error"):
                return result
        except Exception:
            _LOGGER.debug("send_control_async 失败，准备重连重试", exc_info=True)

        try:
            self._protocol.close_server()
        except Exception:
            pass
        try:
            self._protocol.login()
            self._protocol.connect_server()
            return self._protocol.send_control_async(self.did, as_dict)
        except Exception:
            _LOGGER.debug("send_control_async 重试失败", exc_info=True)
            return {"error": "send failed"}

    def _send_lan_control(self, as_dict: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if not getattr(self._protocol, "_local_socket", None):
                success = self._protocol.connect_local(
                    self.local_host, self.local_port, self.lan_pin,
                )
                if not success:
                    _LOGGER.warning("局域网认证返回失败, 回退到云端控制")
                    return self._send_cloud_control(as_dict)
            self._protocol.send_local_control(self.did, as_dict)
            self._last_seen_online = time.time()
            return {"status": "sent"}
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            _LOGGER.debug("LAN socket 断开, 重连重试")
            self._protocol.close_local()
            try:
                success = self._protocol.connect_local(
                    self.local_host, self.local_port, self.lan_pin,
                )
                if not success:
                    _LOGGER.warning("LAN 重连失败, 回退到云端控制")
                    return self._send_cloud_control(as_dict)
                self._protocol.send_local_control(self.did, as_dict)
                self._last_seen_online = time.time()
                return {"status": "sent"}
            except Exception:
                _LOGGER.warning("LAN 重试失败, 回退到云端控制")
                self._protocol.close_local()
                return self._send_cloud_control(as_dict)
        except Exception as exc:
            _LOGGER.warning("局域网控制失败 (%s), 回退到云端控制", exc)
            self._protocol.close_local()
            return self._send_cloud_control(as_dict)

    def turn_on(self) -> None:
        raise NotImplementedError

    def turn_off(self) -> None:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} did={self.did} name={self.name!r} online={self.online}>"


# ======================================================================
# 热水器设备
# ======================================================================
@register_device_type("water_heater")
@register_device_type("热水器")
class WanjialeWaterHeater(WanjialeDevice):
    """万家乐 FW3/BA5/DW3 电热水器专用适配。"""

    platform = "water_heater"
    category_cn = "热水器"

    # ------------------------------------------------------------------
    # FW3/BA5/DW3 DVID 映射
    # ------------------------------------------------------------------
    # 已确认：102 = 设置温度 / 目标温度
    DVID_TARGET_TEMP = "102"

    # 已确认：105 = 当前温度，104 = 当前热水量
    DVID_CURRENT_TEMP = "105"
    DVID_HOT_WATER_AMOUNT = "104"

    # 从你的 HA 属性可见 101=1，疑似电源状态。若开关不对，再改这里。
    DVID_POWER = "101"

    target_temperature: Optional[int] = None
    current_temperature: Optional[int] = None
    hot_water_amount: Optional[int] = None
    is_power_on: Optional[bool] = None
    current_mode: Optional[int] = None
    is_heating: Optional[bool] = None
    fault_code: Optional[int] = None
    is_sterilizing: Optional[bool] = None
    is_boost: Optional[bool] = None
    is_instant_heat: Optional[bool] = None

    _last_control_time: float = 0.0
    CONTROL_COOLDOWN = 1.5

    MIN_TEMP = 30
    MAX_TEMP = 75

    @staticmethod
    def _as_int(as_data: Dict[str, Any], key: str) -> Optional[int]:
        """从 as 数据中安全读取整数。"""
        if key not in as_data:
            return None
        try:
            return int(float(as_data[key]))
        except (TypeError, ValueError):
            _LOGGER.debug("FW3/BA5/DW3: 无法解析 DVID %s=%r", key, as_data.get(key))
            return None

    def refresh(self) -> None:
        super().refresh()

        as_data = self.attributes.get("as", {})
        if not isinstance(as_data, dict):
            return

        in_cooldown = time.time() - self._last_control_time < self.CONTROL_COOLDOWN

        power = self._as_int(as_data, self.DVID_POWER)
        if power is not None and not in_cooldown:
            self.is_power_on = power == 1

        target_temp = self._as_int(as_data, self.DVID_TARGET_TEMP)
        if target_temp is not None and self.MIN_TEMP <= target_temp <= self.MAX_TEMP:
            if not in_cooldown:
                self.target_temperature = target_temp

        current_temp = self._as_int(as_data, self.DVID_CURRENT_TEMP)
        if current_temp is not None and 0 <= current_temp <= self.MAX_TEMP:
            self.current_temperature = current_temp

        hot_water_amount = self._as_int(as_data, self.DVID_HOT_WATER_AMOUNT)
        if hot_water_amount is not None and 0 <= hot_water_amount <= 100:
            self.hot_water_amount = hot_water_amount

    # ------------------------------------------------------------------
    # 控制方法：FW3/BA5/DW3 专用
    # ------------------------------------------------------------------
    def set_power(self, on: bool) -> Dict[str, Any]:
        value = 1 if on else 0
        result = self._send_opt(self.DVID_POWER, value)
        if isinstance(result, dict) and not result.get("error"):
            self.is_power_on = on
            self._last_control_time = time.time()
            self._last_seen_online = time.time()
        return result

    def set_temperature(self, temperature: int) -> Dict[str, Any]:
        """设置 FW3/BA5/DW3 目标温度：直接写 DVID 102。"""
        temp = max(self.MIN_TEMP, min(self.MAX_TEMP, temperature))
        result = self._send_opt(self.DVID_TARGET_TEMP, temp)
        if isinstance(result, dict) and not result.get("error"):
            self.target_temperature = temp
            self._last_control_time = time.time()
            self._last_seen_online = time.time()
        return result

    # 以下功能原插件面向老型号，FW3/BA5/DW3 暂不适配。
    def set_mode(self, mode: int) -> Dict[str, Any]:
        return {"error": "FW3/BA5/DW3 mode control is not mapped"}

    def set_instant_heat(self, on: bool, duration: int = 0) -> Dict[str, Any]:
        return {"error": "FW3/BA5/DW3 instant heat control is not mapped"}

    def set_boost(self, on: bool) -> Dict[str, Any]:
        return {"error": "FW3/BA5/DW3 boost control is not mapped"}


# ======================================================================
# 预留：其他设备类型
# ======================================================================
@register_device_type("range_hood")
@register_device_type("油烟机")
class WanjialeRangeHood(WanjialeDevice):
    platform = "fan"
    category_cn = "油烟机"


@register_device_type("stove")
@register_device_type("灶具")
class WanjialeStove(WanjialeDevice):
    platform = "switch"
    category_cn = "灶具"


@register_device_type("disinfect")
@register_device_type("消毒柜")
class WanjialeDisinfect(WanjialeDevice):
    platform = "switch"
    category_cn = "消毒柜"


# ======================================================================
# 顶层 API：WanjialeApi
# ======================================================================
class WanjialeApi:
    """对 HA 集成暴露的顶层接口。"""

    def __init__(self, protocol: WanjialeProtocol) -> None:
        self._protocol = protocol
        self._devices: List[WanjialeDevice] = []
        self._last_device_list_refresh: float = 0.0
        self._bg_device_list_interval: float = 300.0

    @property
    def devices(self) -> List[WanjialeDevice]:
        return list(self._devices)

    @property
    def protocol(self) -> WanjialeProtocol:
        return self._protocol

    def login(self) -> Dict[str, Any]:
        return self._protocol.login()

    def load_devices(self) -> List[WanjialeDevice]:
        raw_list = self._protocol.get_devices()
        self._devices = []
        for raw in raw_list:
            cls = _resolve_device_class(raw)
            _LOGGER.info(
                "设备分类: did=%s name=%s model=%s → %s",
                raw.get("did"), raw.get("name"), raw.get("model"), cls.__name__,
            )
            self._devices.append(cls(self._protocol, raw))

        # 尝试 UDP 广播发现局域网 IP
        self._discover_lan()

        return self._devices

    def _discover_lan(self) -> None:
        """UDP 广播发现局域网 IP，自动填充 local_host / local_port。"""
        if not self._devices:
            return
        try:
            ip = self._protocol.discover_device(timeout=2.0)
        except Exception:
            _LOGGER.debug("UDP 广播发现失败")
            return
        if not ip:
            return
        for dev in self._devices:
            if not dev.local_host:
                dev.local_host = ip
                dev.local_port = LOCAL_PORT
                _LOGGER.info(
                    "LAN 发现: %s → %s:%d",
                    dev.name, dev.local_host, dev.local_port,
                )

    def _ensure_cloud_connected(self) -> bool:
        """确保云端长连接可用；失败时尝试重新登录后再连接。"""
        if getattr(self._protocol, "_socket", None):
            return True
        try:
            return bool(self._protocol.connect_server())
        except Exception:
            _LOGGER.debug("云端长连接不可用，尝试重新登录", exc_info=True)
        try:
            self._protocol.close_server()
            self._protocol.login()
            return bool(self._protocol.connect_server())
        except Exception:
            _LOGGER.debug("重新登录/重建长连接失败", exc_info=True)
            return False

    def _relogin_and_reconnect_cloud(self) -> bool:
        """强制重新登录并重建云端长连接。"""
        try:
            self._protocol.close_server()
        except Exception:
            pass
        try:
            self._protocol.login()
            return bool(self._protocol.connect_server())
        except Exception:
            _LOGGER.debug("强制重新登录/重连失败", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # 核心：通过 TCP 长连接查询设备状态
    # ------------------------------------------------------------------
    def refresh_all(self) -> None:
        """刷新所有设备状态。

        LAN 用于控制 + 查询回退。云端长连接优先查询。
        任何异常不得穿透此方法——coordinator 成功后实体仍可用。
        """
        try:
            self._refresh_all_impl()
        except Exception:
            _LOGGER.debug("refresh_all 异常", exc_info=True)

    def _try_refresh_device_list(self) -> None:
        """后台线程：HTTP 拉设备列表（不阻塞主 poll 流程）。

        HTTP get_devices 使用独立的短超时(3s)，失败不影响设备状态查询。
        每 _bg_device_list_interval 秒最多执行一次。
        与主线程并发写入 dev 属性是安全的——CPython GIL 保证单条赋值原子性，
        且 dict.update 碰撞概率极低（300s 间隔 vs 3s HTTP），即使碰撞也会在下轮自愈。
        """
        now = time.time()
        if now - self._last_device_list_refresh < self._bg_device_list_interval:
            return
        self._last_device_list_refresh = now

        try:
            raw_list = self._protocol.get_devices()
        except Exception as e:
            _LOGGER.debug("HTTP 刷新设备列表失败: %s", e)
            return

        try:
            self._apply_device_list(raw_list)
        except Exception:
            _LOGGER.debug("_apply_device_list 异常", exc_info=True)

    def _refresh_all_impl(self) -> None:
        if not self._devices:
            return

        if not any(dev.local_host for dev in self._devices):
            self._discover_lan()

        threading.Thread(target=self._try_refresh_device_list, daemon=True).start()

        for dev in self._devices:
            if not dev.online:
                continue
            try:
                result = self._query_device_cloud(dev)
                if isinstance(result, dict) and result.get("error") and dev.is_lan_available():
                    result = self._query_device_lan(dev)
            except Exception:
                if dev.is_lan_available():
                    try:
                        result = self._query_device_lan(dev)
                    except Exception:
                        _LOGGER.debug("查询设备 %s 失败", dev.did)
                        continue
                else:
                    _LOGGER.debug("查询设备 %s 失败", dev.did)
                    continue

            if not isinstance(result, dict) or result.get("error"):
                continue

            as_data = result.get("as", {})
            if isinstance(as_data, dict) and as_data:
                dev._raw["as"] = as_data
                dev.attributes["as"] = as_data
                dev._last_seen_online = time.time()
                dev.refresh()
                _LOGGER.info(
                    "设备状态更新: %s power=%s temp=%s mode=%s",
                    dev.name, getattr(dev, "is_power_on", None),
                    getattr(dev, "current_temperature", None),
                    getattr(dev, "current_mode", None),
                )

    def _query_device_lan(self, dev: WanjialeDevice) -> Dict[str, Any]:
        """通过 LAN 查询设备状态（云连接不可用时的回退方案）。"""
        if not dev.is_lan_available():
            return {"error": "no LAN"}
        try:
            if not getattr(self._protocol, "_local_socket", None):
                success = self._protocol.connect_local(
                    dev.local_host, dev.local_port, dev.lan_pin,
                )
                if not success:
                    return {"error": "lan auth failed"}
            return self._protocol.query_local_device(dev.did, timeout=3)
        except Exception:
            self._protocol.close_local()
            return {"error": "lan query failed"}

    def _query_device_cloud(self, dev: WanjialeDevice) -> Dict[str, Any]:
        """通过云端长连接查询设备状态；长连接失效时自动重连/重新登录。"""
        if not self._ensure_cloud_connected():
            return {"error": "no cloud socket"}
        try:
            result = self._protocol.query_device(dev.did, timeout=3)
            if isinstance(result, dict) and not result.get("error"):
                return result
        except Exception:
            _LOGGER.debug("云端查询失败，准备重新登录后重试", exc_info=True)

        if not self._relogin_and_reconnect_cloud():
            return {"error": "cloud reconnect failed"}
        try:
            return self._protocol.query_device(dev.did, timeout=3)
        except Exception:
            _LOGGER.debug("云端查询重试失败", exc_info=True)
            return {"error": "cloud query failed"}

    async def async_refresh_all(self) -> None:
        """异步刷新（HA coordinator 调用）。"""
        import asyncio
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self.refresh_all)
        except Exception:
            _LOGGER.exception("async_refresh_all 失败")

    def _apply_device_list(self, raw_list: List[Dict[str, Any]]) -> None:
        did_to_device = {dev.did: dev for dev in self._devices}
        now = time.time()
        for raw in raw_list:
            did = str(raw.get("did") or "")
            dev = did_to_device.get(did)
            if dev is not None:
                dev._raw = raw
                dev.name = str(raw.get("name") or dev.name)
                cloud_online = bool(raw.get("online"))
                if not cloud_online and dev.is_lan_available() and dev._last_seen_online > 0:
                    if now - dev._last_seen_online < 120:
                        dev.online = True
                        _LOGGER.debug(
                            "云端报告设备离线但 LAN 可用, 保持在线: %s (%.0fs前确认在线)",
                            dev.name, now - dev._last_seen_online,
                        )
                    else:
                        dev.online = False
                        _LOGGER.info("设备 %s 超时未确认在线, 标记离线", dev.name)
                elif not cloud_online and dev.is_lan_available():
                    dev.online = True
                else:
                    dev.online = cloud_online
                dev.model = str(raw.get("model") or dev.model)
                dev.refresh()
            else:
                _LOGGER.info("发现新设备: %s", did)

    def connect_server(self) -> bool:
        return self._protocol.connect_server()

    def close_server(self) -> None:
        self._protocol.close_server()

    def reconnect(self) -> bool:
        """断线重连。"""
        self.close_server()
        return self.connect_server()

    def get_device_by_did(self, did: str) -> Optional[WanjialeDevice]:
        for dev in self._devices:
            if dev.did == did:
                return dev
        return None
