# -*- coding: utf-8 -*-
"""hand_agent.py —— 跑在 B 机（192.168.1.100）上的「手」代理。

目的：A 机只要连 B:8300 发一行一条文本命令，就能驱动插在 B 上的
      RP2040「中介人」板子（2040zero / RP2040-PiZero）。

板子可能处于两种形态，本代理**自动识别**，命令表对 A 完全一样：

  A) HID 形态（hid-remapper 打过 INJECT 补丁的固件，v18）
     · 走 vendor HID collection：usage_page 0xFF00 / usage 0x20
     · Feature report id=100，32 字节，末尾 crc32
     · 我们自己发鼠标/键盘报文（== 机器人的手）
     · 身份：046D:C547（伪装罗技）或 CAFE:BAF2（原厂）
     · 依赖：middleman/mcu_hid.py（本目录自带一份，原样复制）

  B) 串口形态（CircuitPython 串口版固件 v5.x）
     · USB CDC data 口（COMx），一行一条文本命令
     · 命令表与旧 Pico2W 固件相同：key/combo/text/move/click/scroll/
       sleep/ack/status/usbinfo/bye/reset
     · 老固件对 move/key/text/sleep/scroll 成功时不回话 → 本代理补 OK

命令表（A → B:8300，一行一条，回一行）：
    status                      -> OK v=.. mode=hid|serial inj=0|1 up=..s btn=..
    diag                        -> JSON：枚举到的全部 HID / COM / 板子身份
    move <dx> <dy>              -> 相对移动（HID=计数；serial=原样计数）
    movepx <dx> <dy>            -> 按像素移动（HID 用实测 px/count 换算）
    click <l|r|m> [ms]          -> 按下-保持-松开
    mdown <l|r|m> / mup         -> 按住 / 松开（拖拽）
    key <name>                  -> 单键（tab/enter/esc/space/f1..f12/a-z/0-9/方向键）
    combo <mod>+<key>           -> 组合键，mod=ctrl/alt/shift/win
    text <ascii>                -> 打一串 ASCII
    scroll <n>                  -> 滚轮
    release                     -> 松开所有键和鼠标按钮
    monitor on|off              -> 板子上报真人输入流（HID 形态才有）
    human [ms]                  -> 打印最近 ms 毫秒的真人输入事件
    bye                         -> 断开本连接
    quit                        -> 让代理自己退出（调试用）

自检/留痕（不依赖 TCP，SMB 直接读）：
    C:\\Users\\you\\dsh_hand\\status.txt    每 2 秒覆盖：state/mode/ver/uptime/btn
    C:\\Users\\you\\dsh_hand\\diag.json     启动时 + 收到 diag 时覆盖
    C:\\Users\\you\\dsh_hand\\agent.log     追加日志（>4MB 自动清空重来）

用法（B 上）：
    <便携python> hand_agent.py --diag        只自检，打印/写 diag.json 就退出
    <便携python> hand_agent.py               常驻，监听 0.0.0.0:8300
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import socket
import struct
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = r"C:\Users\you\dsh_hand"
LOG_PATH = os.path.join(WORK, "agent.log")
STATUS_PATH = os.path.join(WORK, "status.txt")
DIAG_PATH = os.path.join(WORK, "diag.json")
CALIB_PATH = os.path.join(WORK, "calib.json")
PID_PATH = os.path.join(WORK, "agent.pid")

TCP_PORT = 8300
MAX_LOG = 4 * 1024 * 1024
SERIAL_REPLY_TIMEOUT = 1.2
SILENT_VERBS = {"move", "key", "text", "sleep", "scroll"}

# B 机自己的眼睛服务（用户会话里跑），只用来读光标 / 摆光标做标定
EYE_BASE = "http://127.0.0.1:18790"
EYE_TOKEN = "razer-eye-2026"
# 项目实测常数：2026-10-24 实测 40 计数 = +100px（眼睛口径），即 2.5 px/计数
DEFAULT_PXPC = 2.5

# 板子身份（两种都要认，回滚固件也不用改）
HID_IDENTITIES = [(0x046D, 0xC547), (0xCAFE, 0xBAF2)]
VENDOR_PAGE = 0xFF00
USAGE_CONFIG = 0x20
USAGE_MONITOR = 0x21


def ensure_work_dir():
    try:
        os.makedirs(WORK, exist_ok=True)
    except Exception:
        pass


def log(*a):
    ensure_work_dir()
    line = time.strftime("[%H:%M:%S] ") + " ".join(str(x) for x in a)
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > MAX_LOG:
            os.replace(LOG_PATH, LOG_PATH + ".1")
    except Exception:
        pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 最小 COM 口封装（B 上没有 pyserial，用 Win32 原始句柄；USB CDC 不关心波特率）
# ---------------------------------------------------------------------------
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = wt.HANDLE
_k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                             wt.DWORD, wt.DWORD, wt.HANDLE]
_k32.ReadFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
_k32.WriteFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
_k32.CloseHandle.argtypes = [wt.HANDLE]


class COMMTIMEOUTS(ctypes.Structure):
    _fields_ = [("ReadIntervalTimeout", wt.DWORD),
                ("ReadTotalTimeoutMultiplier", wt.DWORD),
                ("ReadTotalTimeoutConstant", wt.DWORD),
                ("WriteTotalTimeoutMultiplier", wt.DWORD),
                ("WriteTotalTimeoutConstant", wt.DWORD)]


# 必须声明 argtypes：64 位下句柄不声明会被截成 32 位，SetCommTimeouts 静默失败，
# 之后 ReadFile 就会一直阻塞（串口读挂死的经典原因）。
_k32.SetCommTimeouts.argtypes = [wt.HANDLE, ctypes.POINTER(COMMTIMEOUTS)]
_k32.SetCommTimeouts.restype = wt.BOOL
_k32.ReadFile.restype = wt.BOOL
_k32.WriteFile.restype = wt.BOOL
_k32.CloseHandle.restype = wt.BOOL
_k32.CreateFileW.restype = ctypes.c_void_p


def list_com_ports():
    """注册表 SERIALCOMM：{设备名: COMx}（不需要管理员）。"""
    out = {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DEVICEMAP\SERIALCOMM") as k:
            i = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                out[name] = val
                i += 1
    except Exception:
        pass
    return out


class ComPort:
    def __init__(self, name):
        self.name = name
        self.h = None
        self.buf = b""

    def open(self):
        h = _k32.CreateFileW("\\\\.\\" + self.name, GENERIC_READ | GENERIC_WRITE,
                             0, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
        if not h or h == INVALID_HANDLE_VALUE:
            raise OSError("open %s failed: %d" % (self.name, ctypes.get_last_error()))
        self.h = h
        to = COMMTIMEOUTS(0xFFFFFFFF, 0, 0, 0, 0)      # 读：立刻返回已到的字节
        _k32.SetCommTimeouts(h, ctypes.byref(to))
        return self

    def write(self, data: bytes):
        n = wt.DWORD(0)
        _k32.WriteFile(self.h, data, len(data), ctypes.byref(n), None)

    def read_available(self) -> bytes:
        got = b""
        buf = ctypes.create_string_buffer(4096)
        for _ in range(8):
            n = wt.DWORD(0)
            ok = _k32.ReadFile(self.h, buf, 4096, ctypes.byref(n), None)
            if not ok or n.value == 0:
                break
            got += buf.raw[:n.value]
        return got

    def read_line(self, timeout):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            self.buf += self.read_available()
            if b"\n" in self.buf:
                line, _, self.buf = self.buf.partition(b"\n")
                return line.decode("utf-8", "replace").strip()
            time.sleep(0.01)
        return None

    def drain(self):
        self.buf = b""
        self.read_available()

    def close(self):
        if self.h:
            try:
                _k32.CloseHandle(self.h)
            except Exception:
                pass
            self.h = None


# ---------------------------------------------------------------------------
# 键表
# ---------------------------------------------------------------------------
KEYS = {
    "enter": 0x28, "esc": 0x29, "backspace": 0x2A, "tab": 0x2B, "space": 0x2C,
    "capslock": 0x39, "delete": 0x4C, "insert": 0x49, "home": 0x4A, "end": 0x4D,
    "pageup": 0x4B, "pagedown": 0x4E, "right": 0x4F, "left": 0x50,
    "down": 0x51, "up": 0x52,
    "ctrl": 0xE0, "lctrl": 0xE0, "rctrl": 0xE4,
    "shift": 0xE1, "lshift": 0xE1, "rshift": 0xE5,
    "alt": 0xE2, "lalt": 0xE2, "ralt": 0xE6,
    "win": 0xE3, "lwin": 0xE3, "rwin": 0xE7,
}
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    KEYS[_c] = 0x04 + _i
for _i, _c in enumerate("1234567890"):
    KEYS[_c] = 0x1E + _i
for _i in range(1, 13):
    KEYS["f%d" % _i] = 0x3A + _i - 1
for _c, _u in {"-": 0x2D, "=": 0x2E, "[": 0x2F, "]": 0x30, "\\": 0x31,
               ";": 0x33, "'": 0x34, "`": 0x35, ",": 0x36, ".": 0x37, "/": 0x38}.items():
    KEYS[_c] = _u

BTN = {"l": 1, "left": 1, "r": 2, "right": 2, "m": 4, "middle": 4}


def 光标助手通(端口=8311) -> bool:
    """会话 1 里的光标小服务通不通（本机回环）。"""
    try:
        s = socket.create_connection(("127.0.0.1", 端口), timeout=0.5)
        try:
            s.sendall(b"q\n")
            return s.recv(200).decode("utf-8", "replace").strip().startswith("OK")
        finally:
            s.close()
    except Exception:
        return False


def 读光标_本机(端口=8311) -> str:
    """问会话 1 的小服务：OK x=.. y=.. l=.. r=.. m=..（精确值，不是从画面猜的）。"""
    s = socket.create_connection(("127.0.0.1", 端口), timeout=1.0)
    try:
        s.sendall(b"q\n")
        return s.recv(256).decode("utf-8", "replace").strip()
    finally:
        s.close()


def 问助手(报文: bytes, 端口=8311) -> str:
    """问会话 1 的小服务一句话（b"q\\n" 读光标 / b"f\\n" 问前台窗口标题）。"""
    s = socket.create_connection(("127.0.0.1", 端口), timeout=1.0)
    try:
        s.sendall(报文)
        return s.recv(512).decode("utf-8", "replace").strip()
    finally:
        s.close()


class Board:
    """统一的「手」：HID feature-report 或 USB 串口，A 看到的命令表一致。"""

    def __init__(self):
        self.mode = None            # "hid" / "serial"
        self.hid = None             # mcu_hid.Middleman
        self.com = None             # ComPort
        self.ver = None
        self.inj = False
        self.held = 0
        self.hold_since = 0.0       # ★ 按住开始的时刻 —— 兜底松手用（见 松手_超时）
        self.t0 = time.time()
        self.px_per_count = None
        self.calib_source = ""
        self.last_error = ""
        self.monitor_on = False
        # ---- 让路（真实人输入 → 机器人停手）----
        self.yield_secs = 1.5       # ★ 2026-10-26 12:3x：8.0 → 1.5
        # 为什么改小：B 上实测空闲时仍有约 10 个/分钟的真实 HID 事件（触摸板/无线鼠标接收器抖动）。
        # 8 秒窗口意味着自动化时每隔一会儿就"让路"一次，动作被静默吃掉 28 次（证据\137）。
        # 1.5 秒仍然能挡住"人真的在连续操作"（那种情况事件是每秒上百个），但不吃抖动。
        # 要彻底关掉：`yield off`；要恢复老语义：`yield 8`。
        self.last_human = 0.0       # 最后一次"真人输入"的时间戳
        self.human_events = 0       # 累计真人事件数（看门狗/日志用）
        self.own_until = 0.0        # 我们自己注入的保护窗（窗内的 monitor 事件当自己人）
        self._mon_thread = None

    # ---------- 发现 ----------
    def hid_devices(self):
        try:
            import hid
        except Exception as e:
            return [], "hidapi 不可用: %r" % (e,)
        devs = []
        try:
            allv = hid.enumerate()
        except Exception as e:
            return [], "enumerate 失败: %r" % (e,)
        for d in allv:
            p = d.get("path") or b""
            if isinstance(p, bytes):
                try:
                    p = p.decode("utf-8", "replace")
                except Exception:
                    p = str(p)
            devs.append({
                "vid": "0x%04X" % (d.get("vendor_id") or 0),
                "pid": "0x%04X" % (d.get("product_id") or 0),
                "product": d.get("product_string"),
                "manufacturer": d.get("manufacturer_string"),
                "serial": d.get("serial_number"),
                "usage_page": "0x%04X" % (d.get("usage_page") or 0),
                "usage": "0x%04X" % (d.get("usage") or 0),
                "iface": d.get("interface_number"),
                "path": p[-60:],
            })
        return devs, ""

    def try_hid(self):
        try:
            import mcu_hid
        except Exception as e:
            return False, "mcu_hid 导入失败: %r" % (e,)
        try:
            # ★ want_monitor=True：这块板有 monitor 集合（0xFF00/0x21），
            #   能上报**真人**的原始输入 —— 让路从此是真信号，不是"光标没到位"的推断
            m = mcu_hid.Middleman().open(want_monitor=True)
        except Exception as e:
            try:
                m = mcu_hid.Middleman().open(want_monitor=False)
                log("monitor 集合打不开（%r）→ 退化成只能注入，让路失效" % (e,))
            except Exception as e2:
                return False, "打开失败: %s" % (e2,)
        try:
            v = m.get_version()
            inj = m.has_inject()
        except Exception as e:
            try:
                m.close()
            except Exception:
                pass
            return False, "命令无回: %s" % (e,)
        self.hid = m
        self.mode = "hid"
        self.ver = v
        self.inj = bool(inj)
        self._start_monitor()
        return True, ""

    # ---------- 让路：monitor 通道 = 真人在动的**真信号** ----------
    def monitor_available(self):
        h = self.hid
        return bool(h is not None and getattr(h, "_monitor", None) is not None)

    def _start_monitor(self):
        if not self.monitor_available():
            return
        try:
            self.hid.set_monitor(True)
            self.monitor_on = True
        except Exception as e:
            log("set_monitor(True) 失败：%r" % (e,))
        if self._mon_thread is not None and self._mon_thread.is_alive():
            return
        self._mon_thread = threading.Thread(target=self._mon_loop, name="monitor", daemon=True)
        self._mon_thread.start()
        log("monitor 线程已起（真人输入会让我让路 %.0fs）" % self.yield_secs)

    def _mon_loop(self):
        while True:
            if getattr(self, "_mon_stop", False):
                return
            h = self.hid
            if h is None or getattr(h, "_monitor", None) is None:
                time.sleep(0.5)
                continue
            try:
                evs = h.read_events(30)
            except AttributeError:
                time.sleep(0.3)          # 板子被让出去了（board_off），正常
                continue
            except Exception as e:
                self.last_error = "monitor: %r" % (e,)
                time.sleep(0.3)
                continue
            if not evs:
                continue
            now = time.time()
            if now <= self.own_until:
                continue          # 我们自己刚注入的那一小段，别让路给自己
            self.last_human = now
            self.human_events += len(evs)

    def human_age(self):
        if not self.last_human:
            return None
        return time.time() - self.last_human

    def yielding(self):
        age = self.human_age()
        return bool(age is not None and age < self.yield_secs)

    def yield_block(self, verb):
        """真人在动就拒绝出手；返回拒绝原因，None = 可以动手。"""
        if self.yield_secs <= 0:
            return None
        age = self.human_age()
        if age is not None and age < self.yield_secs:
            return ("ERR yield human-active age=%.1fs window=%.0fs"
                    "（你在动鼠标，我先让路；停手 %.0fs 后自己恢复）"
                    % (age, self.yield_secs, self.yield_secs))
        return None

    def try_serial(self):
        ports = list_com_ports()
        cand = [v for v in ports.values()]
        tried = []
        for name in cand:
            c = ComPort(name)
            try:
                c.open()
            except Exception as e:
                tried.append("%s:open失败" % name)
                continue
            try:
                c.drain()
                c.write(b"status\n")
                line = c.read_line(0.8)
            except Exception as e:
                line = None
            if line and line.upper().startswith("OK"):
                self.com = c
                self.mode = "serial"
                vv = None
                for tok in line.split():
                    if tok.lower().startswith("v="):
                        try:
                            vv = tok.split("=", 1)[1]
                        except Exception:
                            pass
                self.ver = vv
                self.inj = False
                return True, line
            tried.append("%s:无OK回" % name)
            c.close()
        return False, "；".join(tried) if tried else "没有 COM 口"

    def detect(self):
        """先 HID 后串口；都失败就记原因。"""
        self.close()
        self.ver = None
        self.inj = False
        ok, why = self.try_hid()
        if ok:
            return True
        hid_why = why
        ok, why2 = self.try_serial()
        if ok:
            self.last_error = "HID: %s / serial: %s" % (hid_why, why2)
            return True
        self.mode = None
        self.last_error = "HID: %s / serial: %s" % (hid_why, why2)
        return False

    def ensure(self):
        if getattr(self, "hw_off", False):
            return False          # 硬件已让给别的程序（单机版），不许偷偷抢回来
        if self.hid is not None or self.com is not None:
            return True
        return self.detect()

    def 松手(self, why="") -> bool:
        """★ B 侧兜底：把按着的鼠标按钮松开，别的什么都不做。

        为什么必须有它（2026-10-26 12:3x）：
            松手原先只有一个调用点（`board_off`），A 的进程崩了 / 网断了 / 被强杀时，
            B 会**一直按着**，角色就自己跑下去 —— 跑到撞墙、跑出安全区都停不下来。
            最后一厘米的权威必须留在 B：谁来不及说"放手"，B 自己放。

        返回值：真的松了东西 → True。
        """
        if not self.held:
            return False
        self.held = 0
        self.hold_since = 0.0
        try:
            self._hid_mouse(buttons=0)
        except Exception as e:
            log("兜底松手时板子出错（已吞掉）：%r" % (e,))
        log("★ 兜底松手（%s）" % (why or "未说原因"))
        return True

    def release_hardware(self, why=""):
        """把板子让出去：停 monitor 流 + 关句柄，但代理本身继续跑（保命令口/看门狗/会话启动）。"""
        self.hw_off = True
        self._mon_stop = True
        try:
            if self.hid is not None:
                self.hid.set_monitor(False)
        except Exception:
            pass
        self.close()
        self.monitor_on = False
        log("板子已释放给别的程序（%s）" % (why or "手动"))
        return "OK board_off（板子已让出，DshHand 仍在跑）"

    def acquire_hardware(self):
        self.hw_off = False
        self._mon_stop = False
        self._mon_thread = None
        ok = self.detect()
        return ("OK board_on" if ok else "ERR board_on " + self.last_error)

    def close(self):
        if self.hid is not None:
            try:
                self.hid.close()
            except Exception:
                pass
            self.hid = None
        if self.com is not None:
            try:
                self.com.close()
            except Exception:
                pass
            self.com = None
        self.mode = None

    # ---------- 底层动作 ----------
    def _hid_mouse(self, dx=0, dy=0, wheel=0, buttons=None):
        self.hid.inject_mouse(dx=dx, dy=dy, wheel=wheel, buttons=buttons)

    def _hid_keys(self, keys):
        self.hid.inject_key(keys)

    def _ser(self, text):
        self.com.write((text + "\r\n").encode("utf-8", "replace"))

    def _serial_cmd(self, text):
        verb = text.split(" ", 1)[0].strip().lower()
        self.com.drain()
        self._ser(text)
        if verb in SILENT_VERBS:
            line = self.com.read_line(0.12)
            return line if line else "OK"
        line = self.com.read_line(SERIAL_REPLY_TIMEOUT)
        if line is None:
            raise RuntimeError("串口命令无回：%s" % text)
        return line

    def _px_scale(self):
        if self.px_per_count:
            return self.px_per_count
        # 只用我们自己的标定文件；**不信** B 上原控制器的 runtime/middleman_calib.json
        # （实测它写的是 1.000，而真值是 2.5 —— 用它 movepx 会差 2.5 倍）
        try:
            if os.path.exists(CALIB_PATH):
                d = json.load(open(CALIB_PATH, "r", encoding="utf-8"))
                v = d.get("px_per_count")
                if isinstance(v, (int, float)) and v > 0:
                    self.px_per_count = float(v)
                    self.calib_source = "file"
                    return self.px_per_count
        except Exception:
            pass
        return self.do_calibrate()

    def save_calib(self, value):
        self.px_per_count = float(value)
        self.calib_source = "manual"      # 否则 status 会印出「1.600(未标定)」——值对、标签错
        ensure_work_dir()
        try:
            with open(CALIB_PATH, "w", encoding="utf-8") as f:
                json.dump({"px_per_count": self.px_per_count,
                           "time": time.strftime("%Y-%m-%dT%H:%M:%S")},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return self.px_per_count

    def do_calibrate(self, counts=200, samples=3):
        """实测「一个鼠标计数 = 多少屏幕像素」。

        坑：本代理跑在 **会话 0**（服务），GetCursorPos 读到的是会话 0 自己的桌面，
        不是用户桌面的光标 —— 所以必须借 B 自己的眼睛服务（用户会话里跑的
        razer-eye，127.0.0.1:18790）来读光标 / 摆光标。
        """
        if self.mode != "hid" or self.hid is None:
            self.px_per_count = 1.0
            return self.px_per_count

        ratios = []
        try:
            for _ in range(samples):
                if not self.eye_move(400, 900):
                    break
                time.sleep(0.05)
                p0 = self.eye_cursor()
                if not p0:
                    break
                self.hid.inject_mouse(dx=counts)
                time.sleep(0.12)
                p1 = self.eye_cursor()
                if not p1:
                    break
                dx = p1[0] - p0[0]
                if dx > 0:
                    ratios.append(dx / float(counts))
        except Exception as e:
            self.last_error = "标定失败 %r" % (e,)

        if ratios:
            ratios.sort()
            self.save_calib(ratios[len(ratios) // 2])
            self.calib_source = "measured"
        else:
            # 眼睛不在：用项目实测常数 2.5（2026-10-24 实测 40 计数 = +100px）
            self.save_calib(DEFAULT_PXPC)
            self.calib_source = "default(2.5, eyes offline)"
        return self.px_per_count

    # ---------- 借眼睛服务（用户会话）读/摆光标 ----------
    def _eye(self, path, payload=None):
        import urllib.request
        url = EYE_BASE + path
        data = None
        headers = {"X-Token": EYE_TOKEN}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))

    def eye_cursor(self):
        try:
            d = self._eye("/cursor")
            c = d.get("cursor") or {}
            return int(c.get("x", 0)), int(c.get("y", 0))
        except Exception:
            return None

    def eye_move(self, x, y):
        try:
            self._eye("/move", {"x": int(x), "y": int(y)})
            return True
        except Exception:
            return False

    # ---------- 命令 ----------
    def cmd_status(self):
        if getattr(self, "hw_off", False):
            age = self.human_age()
            return ("OK board=off（板子已让给单机版；本代理仍在跑）"
                    " yield=%.0fs human_age=%s events=%d"
                    % (self.yield_secs,
                       ("%.1fs" % age) if age is not None else "none",
                       self.human_events))
        ok = self.ensure()
        if not ok:
            return "ERR board not found | " + self.last_error
        if self.px_per_count is None:          # 只读标定文件，不触发实测（不挪光标）
            try:
                if os.path.exists(CALIB_PATH):
                    d = json.load(open(CALIB_PATH, "r", encoding="utf-8"))
                    v = d.get("px_per_count")
                    if isinstance(v, (int, float)) and v > 0:
                        self.px_per_count = float(v)
                        self.calib_source = "file"
            except Exception:
                pass
        up = int(time.time() - self.t0)
        return ("OK v=%s mode=%s inj=%d up=%ds btn=%d pxpc=%.3f(%s) "
                "yield=%.0fs human_age=%s events=%d mon=%d"
                % (self.ver, self.mode, 1 if self.inj else 0, up, self.held,
                   self.px_per_count or 0.0, self.calib_source or "未标定",
                   self.yield_secs,
                   ("%.1fs" % self.human_age()) if self.human_age() is not None else "none",
                   self.human_events,
                   1 if self.monitor_on else 0))

    def cmd_diag(self):
        devs, hid_err = self.hid_devices()
        ports = list_com_ports()
        found = self.detect()
        d = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "host": os.environ.get("COMPUTERNAME"),
            "python": sys.version.split()[0],
            "exe": sys.executable,
            "board_found": bool(found),
            "mode": self.mode,
            "version": self.ver,
            "inject": bool(self.inj),
            "last_error": self.last_error,
            "com_ports": ports,
            "hid_error": hid_err,
            "hid_devices": devs,
            "vendor_collections": [x for x in devs if x["usage_page"] == "0xFF00"],
            "identities_present": [x for x in devs
                                   if (x["vid"], x["pid"]) in (("0x046D", "0xC547"),
                                                               ("0xCAFE", "0xBAF2"))],
        }
        ensure_work_dir()
        try:
            with open(DIAG_PATH, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return "OK diag " + json.dumps({k: d[k] for k in
                                        ("board_found", "mode", "version", "inject",
                                         "com_ports", "last_error")}, ensure_ascii=False)

    def cmd(self, line):
        t = line.strip()
        if not t:
            return None
        p = t.split()
        c = p[0].lower()
        try:
            if c == "status":
                return self.cmd_status()
            if c in ("cursor", "cur"):
                # ★ 精确光标由会话 1 里的小服务回答（SYSTEM 在会话 0 读到的光标是错的）
                try:
                    return 读光标_本机()
                except Exception as e:
                    return "ERR cursor 助手没起（%r）" % (e,)
            if c in ("fg", "front"):
                # ★ 会话 1 的前台窗口标题 —— 按键前必须核对（按键只送前台）
                try:
                    return 问助手(b"f\n")
                except Exception as e:
                    return "ERR fg 助手没起（%r）" % (e,)
            if c == "diag":
                return self.cmd_diag()
            if c == "quit":
                return "OK bye"
            if c == "bye":
                return "OK bye"

            # ---- 板子移交 / 会话内启动（不碰硬件也能用，放在 ensure 闸之前）----
            if c == "board_off":
                return self.release_hardware("board_off 命令")

            if c == "board_on":
                return self.acquire_hardware()

            if c == "sessinfo":
                try:
                    import launch_in_session as lis
                    return "OK session=%d（winsta0\\default 可启动）" % lis.active_session()
                except Exception as e:
                    return "ERR sessinfo %r" % (e,)

            if c == "runsas":
                cmdline = t.split(" ", 1)[1] if " " in t else ""
                if not cmdline:
                    return "ERR runsas <命令行>"
                try:
                    import launch_in_session as lis
                    pid, how, err = lis.launch(cmdline)
                    if pid:
                        return "OK runsas pid=%d via %s" % (pid, how)
                    return "ERR runsas %s" % err
                except Exception as e:
                    return "ERR runsas %r" % (e,)

            if not self.ensure():
                return "ERR board not found | " + self.last_error

            # ---- 让路闸：真人还在动就不出手（release/mup 不受限，安全优先）----
            if c in ("move", "movepx", "click", "mdown", "key", "combo", "text", "scroll"):
                blocked = self.yield_block(c)
                if blocked:
                    return blocked
                self.own_until = time.time() + 0.25   # 别让路给我们自己的注入

            if c == "yield":
                arg = p[1].lower() if len(p) > 1 else ""
                if arg == "on":
                    self.yield_secs = 8.0
                elif arg == "off":
                    self.yield_secs = 0.0
                elif arg:
                    try:
                        self.yield_secs = max(0.0, min(3600.0, float(arg)))
                    except Exception:
                        return "ERR yield [on|off|<秒>]"
                return "OK yield %.1fs" % self.yield_secs

            if c in ("ack", "verbose"):
                # 操作台的保活 / 调试用：必须回 OK，否则它会把连接标成不健康
                return "OK " + c

            if c == "sleep":
                time.sleep(max(0, min(3000, int(p[1]) if len(p) > 1 else 100)) / 1000.0)
                return "OK sleep"

            if c == "reset":
                # 这块板不是 Pico2W，reset 只能让它重新枚举 USB（目标程序会丢鼠标锁定）。
                # 老实回 ERR，让操作台按"不支持"处理，而不是假装成功。
                return "ERR reset unsupported on 2040 (HID feature-report board)"

            if c == "usbinfo":
                if self.mode == "hid":
                    return "OK usbinfo vid=046D pid=C547 hid=1 mode=hid"
                return "OK usbinfo mode=%s" % self.mode

            if c == "move":
                dx, dy = int(p[1]), int(p[2])
                if self.mode == "hid":
                    self._hid_mouse(dx=dx, dy=dy)
                    return "OK"
                return self._serial_cmd("move %d %d" % (dx, dy))

            if c == "calibrate":
                return "OK calibrate pxpc=%.4f" % self.do_calibrate()

            if c == "calib":
                if len(p) < 2:
                    return "ERR calib <px_per_count>"
                return "OK calib pxpc=%.4f" % self.save_calib(float(p[1]))

            if c == "movepx":
                dx, dy = int(p[1]), int(p[2])
                sc = self._px_scale()
                if self.mode == "hid":
                    self._hid_mouse(dx=round(dx / sc), dy=round(dy / sc))
                    return "OK"
                return self._serial_cmd("move %d %d" % (round(dx / sc), round(dy / sc)))

            if c == "click":
                mask = BTN.get(p[1].lower() if len(p) > 1 else "l")
                if not mask:
                    return "ERR click"
                ms = int(p[2]) if len(p) > 2 else 80
                ms = max(5, min(2000, ms))
                if self.mode == "hid":
                    self.held |= mask
                    self.hold_since = time.time()
                    self._hid_mouse(buttons=self.held)
                    time.sleep(ms / 1000.0)
                    self.held &= ~mask
                    self.hold_since = time.time() if self.held else 0.0
                    self._hid_mouse(buttons=self.held)
                    return "OK click %dms" % ms
                name = {1: "l", 2: "r", 4: "m"}[mask]
                return self._serial_cmd("click %s" % name)

            if c == "mdown":
                mask = BTN.get(p[1].lower() if len(p) > 1 else "l")
                if not mask:
                    return "ERR mdown"
                if self.mode == "hid":
                    self.held |= mask
                    self.hold_since = time.time()
                    self._hid_mouse(buttons=self.held)
                    return "OK mdown"
                return self._serial_cmd("mdown %s" % {1: "l", 2: "r", 4: "m"}[mask])

            if c in ("mup", "mupall"):
                if self.mode == "hid":
                    self.held = 0
                    self.hold_since = 0.0
                    self._hid_mouse(buttons=0)
                    return "OK mup"
                return self._serial_cmd("mupall")

            if c == "key":
                name = p[1].lower() if len(p) > 1 else ""
                if self.mode == "hid":
                    u = KEYS.get(name)
                    if u is None:
                        return "ERR key %s" % name
                    self._hid_keys([(u, True)])
                    time.sleep(0.02)
                    self._hid_keys([(u, False)])
                    return "OK"
                return self._serial_cmd("key %s" % name)

            if c == "combo":
                spec = p[1] if len(p) > 1 else ""
                if "+" not in spec:
                    return "ERR combo"
                *mods, keyname = spec.split("+")
                if self.mode == "hid":
                    mods_u = [KEYS.get(m.lower()) for m in mods]
                    ku = KEYS.get(keyname.lower())
                    if ku is None or any(m is None for m in mods_u):
                        return "ERR combo names"
                    seq = [(m, True) for m in mods_u] + [(ku, True)]
                    self._hid_keys(seq)
                    time.sleep(0.03)
                    self._hid_keys([(m, False) for m in mods_u] + [(ku, False)])
                    return "OK"
                return self._serial_cmd("combo %s" % spec)

            if c == "text":
                s = t.split(" ", 1)[1] if " " in t else ""
                if self.mode == "hid":
                    if not hasattr(self.hid, "type_text"):
                        return "ERR no type_text"
                    self.hid.type_text(s)
                    return "OK"
                return self._serial_cmd("text %s" % s)

            if c == "scroll":
                n = int(p[1]) if len(p) > 1 else 0
                if self.mode == "hid":
                    self._hid_mouse(wheel=n)
                    return "OK"
                return self._serial_cmd("scroll %d" % n)

            if c == "release":
                if self.mode == "hid":
                    self.held = 0
                    self._hid_mouse(buttons=0)
                    for u in (0xE0, 0xE1, 0xE2, 0xE3):
                        self._hid_keys([(u, False)])
                    self._hid_keys([(u, False) for u in sorted(set(KEYS.values()))[:20]])
                    return "OK release"
                return self._serial_cmd("release")

            if c == "monitor":
                want = (len(p) > 1 and p[1].lower() in ("on", "1", "true"))
                if self.mode != "hid":
                    return "ERR monitor hid-only"
                if want and self.hid._monitor is None:
                    return "ERR monitor not opened (restart agent with monitor)"
                self.hid.set_monitor(want)
                self.monitor_on = want
                return "OK monitor %s" % ("on" if want else "off")

            if c == "human":
                # 只看后台 monitor 线程记的状态：这里再 read_events 会和它抢同一个集合
                age = self.human_age()
                return ("OK human age=%s events=%d yield=%.0fs mon=%d"
                        % (("%.2fs" % age) if age is not None else "none",
                           self.human_events, self.yield_secs,
                           1 if self.monitor_on else 0))

            return "ERR cmd " + c
        except Exception as e:
            self.last_error = repr(e)
            log("命令失败", t, repr(e))
            if self.hid is not None or self.com is not None:
                # 板子可能掉了：关掉，下次命令重新探测
                self.close()
            return "ERR %s" % (e,)


# ---------------------------------------------------------------------------
# TCP 服务（与旧协议一致：一行一条，回一行，单客户端）
# ---------------------------------------------------------------------------
class Server:
    def __init__(self, board):
        self.b = board
        self.cmd_lock = threading.Lock()
        self.busy = threading.Lock()
        self.stop_flag = threading.Event()
        self.hang_until = 0.0          # 调试用：这段时间内不写心跳（测看门狗）
        self.n_clients = 0

    def serve_client(self, conn, addr):
        if not self.busy.acquire(blocking=False):
            try:
                conn.sendall(b"ERR busy\n")
                conn.close()
            except Exception:
                pass
            log("拒掉旁路连接（已有客户端）", addr)
            return
        self.n_clients += 1
        cid = self.n_clients
        log("A 连入 #%d" % cid, addr)
        conn.settimeout(0.5)
        buf = b""
        why = "?"
        try:
            while not self.stop_flag.is_set():
                try:
                    d = conn.recv(4096)
                except socket.timeout:
                    continue
                except Exception as e:
                    why = "recv异常 %r" % (e,)
                    break
                if not d:
                    why = "对端关闭(EOF)"
                    break
                buf += d
                while b"\n" in buf:
                    raw, _, buf = buf.partition(b"\n")
                    text = raw.decode("utf-8", "replace").strip()
                    if not text:
                        continue
                    if text.lower() == "quit":
                        self.stop_flag.set()
                    if text.lower().startswith("hang"):
                        # 调试用：故意停掉心跳，验看门狗会不会把我和服务一起救回来
                        try:
                            sec = int(text.split()[1])
                        except Exception:
                            sec = 40
                        self.hang_until = time.time() + sec
                        reply = "OK hang %ds（心跳已停，等看门狗）" % sec
                    else:
                        with self.cmd_lock:
                            reply = self.b.cmd(text)
                    if reply:
                        try:
                            conn.sendall((reply + "\n").encode("utf-8", "replace"))
                        except Exception as e:
                            why = "回包失败 %r" % (e,)
                            return
        finally:
            try:
                conn.close()
            except Exception:
                pass
            # ★ 兜底松手：A 掉线/崩溃/被强杀时，不许让板子停在"按住"状态
            try:
                self.b.松手("A 断开 #%d" % cid)
            except Exception as e:
                log("断开松手出错（已吞掉）：%r" % (e,))
            log("A 断开 #%d（%s）" % (cid, why), addr)
            self.busy.release()

    def status_writer(self):
        while not self.stop_flag.is_set():
            if time.time() < self.hang_until:
                time.sleep(1.0)
                continue
            b = self.b
            txt = ("%s state=%s mode=%s ver=%s inj=%d up=%ds btn=%d clients=%d last=%s\n"
                   % (time.strftime("%Y-%m-%dT%H:%M:%S"),
                      "ready" if (b.hid or b.com) else "no-board",
                      b.mode, b.ver, 1 if b.inj else 0, int(time.time() - b.t0),
                      b.held, self.n_clients, b.last_error[:120]))
            try:
                with open(STATUS_PATH, "w", encoding="utf-8") as f:
                    f.write(txt)
            except Exception:
                pass
            time.sleep(1.0)

    # ---------- 看门狗（独立进程，防"hidapi 卡死把整个进程冻住"） ----------
    def watchdog_alive(self):
        try:
            p = os.path.join(WORK, "watchdog.alive")
            return (time.time() - os.path.getmtime(p)) < 15
        except Exception:
            return False

    def _助手版本对(self, 端口=8311) -> bool:
        """助手答得出 `f`（前台窗口）才算新版本。老版本只认 q。"""
        try:
            rep = 问助手(b"f\n", 端口)
            return rep.startswith("OK fg=")
        except Exception:
            return False

    def _杀掉端口占用(self, 端口: int):
        """把监听这个端口的进程杀掉（B 上只有我们自己用这个端口）。"""
        try:
            import subprocess
            out = subprocess.run(["netstat", "-ano"], capture_output=True,
                                 text=True, errors="replace", timeout=10).stdout or ""
            pids = set()
            for line in out.splitlines():
                if (":%d" % 端口) in line and "LISTENING" in line.upper():
                    段 = line.split()
                    if 段 and 段[-1].isdigit():
                        pids.add(段[-1])
            for pid in pids:
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True, timeout=10)
                log("杀掉旧的助手进程 pid=%s（占着 %d）" % (pid, 端口))
        except Exception as e:
            log("杀端口占用失败（已吞掉）：%r" % (e,))

    def 按住看门(self, 上限秒=5.0):
        """按住超过 `上限秒` 还没松 → 强制松手。

        为什么要它：`click r 2000` 这种长按，如果 A 在按住期间崩掉，
        连接会断，`finally` 里能兜一次；但如果 A 只是**卡住**（不崩不断），
        B 就会一直举着手。这条线程保证最多 5 秒一定松。
        正常长跑最长约 3 秒，5 秒不会误伤。
        """
        while not self.stop_flag.wait(0.4):
            try:
                since = getattr(self.b, "hold_since", 0.0)
                if getattr(self.b, "held", 0) and since and time.time() - since > 上限秒:
                    self.b.松手("按住 %.1fs 没人松手" % (time.time() - since))
            except Exception:
                pass


    def 确保光标助手(self) -> bool:
        """在**会话 1**里起 cursor_server.py（只读光标，不截屏）。

        为什么要：A 过去只能从采集画面里用模板"猜"光标在哪，既慢又会失败。
        B 本地一句 GetCursorPos 就是精确值。但 DshHand 是 SYSTEM 服务，
        跑在会话 0，直接读会拿到错的光标 —— 所以必须用 launch_in_session
        把助手投进会话 1。

        ★ 版本自愈（2026-10-26 13:0x 踩出来的）：
          助手改了代码之后重启服务，旧助手进程还占着 8311，新助手起不来，
          结果一问还是老行为（"只认 q / bye"）。所以先探版本：
          答不上 `f` 就把占用端口的进程杀掉，再拉新的。
          否则每次改助手都要手去 B 上杀进程。

        失败不影响手：读不到光标，A 还可以退回模板匹配。
        """
        if self._助手版本对():
            return True
        self._杀掉端口占用(8311)
        try:
            import subprocess
            launcher = os.path.join(HERE, "launch_in_session.py")
            helper = os.path.join(HERE, "cursor_server.py")
            if not (os.path.exists(launcher) and os.path.exists(helper)):
                log("!! 缺 launch_in_session.py 或 cursor_server.py，跳过光标助手")
                return False
            cmd = '"%s" -X utf8 "%s"' % (sys.executable, helper)
            r = subprocess.run([sys.executable, launcher, "--cmd", cmd],
                               capture_output=True, timeout=25)
            out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace").strip()
            log("光标助手：%s" % out[:200])
            for _ in range(12):
                if 光标助手通():
                    log("光标助手已就绪（127.0.0.1:8311）")
                    return True
                time.sleep(0.5)
            log("!! 光标助手没起来（8311 不通）")
        except Exception as e:
            log("光标助手起不来（已吞掉）：%r" % (e,))
        return False

    def spawn_watchdog(self):
        if self.watchdog_alive():
            log("看门狗已在跑，不再拉")
            return
        wd = os.path.join(HERE, "hand_watchdog.py")
        if not os.path.exists(wd):
            log("!! 找不到 hand_watchdog.py，跳过看门狗")
            return
        try:
            import subprocess
            flags = 0
            for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
                flags |= getattr(subprocess, name, 0)
            subprocess.Popen([sys.executable, wd, "--pid", str(os.getpid()), "--stale", "20"],
                             cwd=WORK, creationflags=flags, close_fds=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log("已拉起看门狗（独立进程，心跳 20s 不更新就杀我并重启服务）")
        except Exception as e:
            log("拉起看门狗失败：%r" % (e,))

    def run(self):
        ensure_work_dir()
        try:
            with open(PID_PATH, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
        log("=== hand_agent 启动 pid=%d exe=%s ===" % (os.getpid(), sys.executable))
        ok = self.b.detect()
        log("板子探测：%s mode=%s ver=%s inj=%s | %s"
            % ("找到" if ok else "没找到", self.b.mode, self.b.ver,
               1 if self.b.inj else 0, self.b.last_error))
        threading.Thread(target=self.status_writer, daemon=True).start()
        threading.Thread(target=self.按住看门, daemon=True).start()   # ★ 按住超时兜底
        self.确保光标助手()                                          # ★ 会话1里起读光标小服务
        self.spawn_watchdog()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # 独占端口：绝不允许两个实例共用 8300（SO_REUSEADDR 会让旧实例抢走连接，
        # 表现成"代理在跑却没回应" —— 2026-10-24 13:06 那次红手就是这么来的）
        try:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        except Exception:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("0.0.0.0", TCP_PORT))
        except OSError as e:
            log("!! 绑定 %d 失败：%r（很可能已有实例在跑）→ 退出" % (TCP_PORT, e))
            return
        srv.listen(8)
        log("监听 0.0.0.0:%d（独占）" % TCP_PORT)
        srv.settimeout(1.0)
        while not self.stop_flag.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except Exception as e:
                log("accept 出错", repr(e))
                time.sleep(0.5)
                continue
            threading.Thread(target=self.serve_client, args=(conn, addr), daemon=True).start()
        log("=== hand_agent 退出 ===")


def main():
    ensure_work_dir()
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    b = Board()
    if arg in ("--diag", "-d", "--probe"):
        # 先把 diag.json 落盘，再打日志：服务方式启动时没有控制台，print 可能没有 stdout
        b.cmd_diag()
        log("diag: cwd=%s python=%s exe=%s" % (os.getcwd(), sys.version.split()[0], sys.executable))
        ports = list_com_ports()
        log("diag: COM ports=%s" % (ports if ports else "(none)"))
        ok = b.mode is not None
        log("diag: board_found=%s mode=%s ver=%s inject=%s | %s"
            % (ok, b.mode, b.ver, b.inj, b.last_error))
        log("diag: 写入 %s" % DIAG_PATH)
        return 0 if ok else 2
    if arg == "--service":
        return run_as_service(b)
    Server(b).run()
    return 0


# ---------------------------------------------------------------------------
# 真·Windows 服务模式
#   sc 直接启动 python.exe 时，1053 超时后 SCM 会 TerminateProcess；
#   `cmd /c start` 这种方式在 session 0 里不可靠。所以这里自己实现 SCM 协议：
#   注册 handler、报 SERVICE_RUNNING、收到 STOP 时干净退出。
# ---------------------------------------------------------------------------
SERVICE_WIN32_OWN_PROCESS = 0x10
SERVICE_START_PENDING = 0x02
SERVICE_RUNNING = 0x04
SERVICE_STOPPED = 0x01
SERVICE_ACCEPT_STOP = 0x01
SERVICE_CONTROL_STOP = 0x01
SERVICE_CONTROL_INTERROGATE = 0x04


class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [("dwServiceType", wt.DWORD),
                ("dwCurrentState", wt.DWORD),
                ("dwControlsAccepted", wt.DWORD),
                ("dwWin32ExitCode", wt.DWORD),
                ("dwServiceSpecificExitCode", wt.DWORD),
                ("dwCheckPoint", wt.DWORD),
                ("dwWaitHint", wt.DWORD)]


class SERVICE_TABLE_ENTRY(ctypes.Structure):
    _fields_ = [("lpServiceName", wt.LPWSTR),
                ("lpServiceProc", ctypes.c_void_p)]


_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
LPHANDLER_FUNCTION_EX = ctypes.WINFUNCTYPE(wt.DWORD, wt.DWORD, wt.DWORD,
                                           ctypes.c_void_p, ctypes.c_void_p)
SERVICE_MAIN_FUNCTION = ctypes.WINFUNCTYPE(None, wt.DWORD, ctypes.POINTER(wt.LPWSTR))
_advapi32.RegisterServiceCtrlHandlerExW.restype = wt.HANDLE
_advapi32.RegisterServiceCtrlHandlerExW.argtypes = [wt.LPCWSTR, LPHANDLER_FUNCTION_EX, ctypes.c_void_p]
_advapi32.SetServiceStatus.restype = wt.BOOL
_advapi32.SetServiceStatus.argtypes = [wt.HANDLE, ctypes.POINTER(SERVICE_STATUS)]
_advapi32.StartServiceCtrlDispatcherW.restype = wt.BOOL
_advapi32.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(SERVICE_TABLE_ENTRY)]


def run_as_service(board):
    """SCM 入口。StartServiceCtrlDispatcher 会阻塞到服务停止。"""
    state = {"handle": None, "server": None}

    def report(st, accepted=0):
        status = SERVICE_STATUS(SERVICE_WIN32_OWN_PROCESS, st, accepted, 0, 0, 0, 0)
        try:
            _advapi32.SetServiceStatus(state["handle"], ctypes.byref(status))
        except Exception:
            pass

    def handler(control, event_type, event_data, context):
        if control == SERVICE_CONTROL_STOP:
            report(SERVICE_STOPPED)
            srv = state.get("server")
            if srv is not None:
                srv.stop_flag.set()
            return 0
        if control == SERVICE_CONTROL_INTERROGATE:
            report(SERVICE_RUNNING, SERVICE_ACCEPT_STOP)
            return 0
        return 0

    cb = LPHANDLER_FUNCTION_EX(handler)

    def service_main(argc, argv):
        try:
            state["handle"] = _advapi32.RegisterServiceCtrlHandlerExW(
                "DshHand", cb, None)
            report(SERVICE_START_PENDING)
            log("=== hand_agent 以服务方式启动 pid=%d ===" % os.getpid())
            srv = Server(board)
            state["server"] = srv
            report(SERVICE_RUNNING, SERVICE_ACCEPT_STOP)
            srv.run()
        except Exception:
            log("服务主体异常:\n" + traceback.format_exc())
        finally:
            report(SERVICE_STOPPED)

    proc = SERVICE_MAIN_FUNCTION(service_main)
    table = (SERVICE_TABLE_ENTRY * 2)()
    table[0].lpServiceName = "DshHand"
    table[0].lpServiceProc = ctypes.cast(proc, ctypes.c_void_p)
    table[1].lpServiceName = None
    table[1].lpServiceProc = None
    ok = _advapi32.StartServiceCtrlDispatcherW(table)
    if not ok:
        log("StartServiceCtrlDispatcher 失败：err=%d（这不是服务上下文？）"
            % ctypes.get_last_error())
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception:
        log("顶层异常:\n" + traceback.format_exc())
        sys.exit(1)
