# -*- coding: utf-8 -*-
"""手 —— 连到某台机器的代理（TCP 8300），一行命令换一行回执。

协议是现成的（见 手\\部署.md §5），这里只包一层，解决四件真实会咬人的事：

1. **单客户端**：一块板只伺候一个连接。第二个连上来不是「不通」，是 `ERR busy`。
   所以本模块默认**短连接**：连 → 发 → bye → 断开。
   脑绝不长期握着连接 —— 否则脑一崩，板子就被一个死 socket 占着，谁都进不来。

2. **收尾必须 release**：mdown 之后不 mup，宿主会静默吞掉后面所有点击。
   所以 `close()` 一定先发 `release` 再 `bye`；异常路径也一样。

3. **读不到就报错，不猜**：空回包（对端已断）→ 抛错，别把空串当成功。

4. **命令分级**：只读命令（status/diag/release/monitor off）永远放行；
   移动/点击/按键要过 `安全` 开关 —— 闸门在 `guard()` 里，一个地方判，别各写一套。
"""
from __future__ import annotations

import json
import os
import socket
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG_PATH = os.path.join(ROOT, "配置.json")

READONLY = ("status", "diag", "release", "human", "monitor off", "bye", "quit")


class HandError(RuntimeError):
    """手这一路的通用错误。"""


class HandBusy(HandError):
    """板子被另一个客户端占着（单客户端规矩），不是网络不通。"""


def load_cfg(path: str | None = None) -> dict:
    with open(path or CFG_PATH, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def node_addr(cfg: dict, node: str) -> tuple[str, int]:
    """'A' / 'B' → (host, port)。查不到就报错，绝不退回某个默认地址。"""
    spec = (cfg.get("手") or {}).get(node.upper())
    if not spec:
        raise HandError("配置.json 里没有节点 %r（现有：%s）"
                        % (node, ", ".join(k for k in (cfg.get("手") or {}) if not k.startswith("_"))))
    return str(spec["地址"]), int(spec.get("端口", 8300))


def is_readonly(line: str) -> bool:
    first = line.strip().split(" ", 1)[0].lower()
    if first in ("status", "diag", "release", "human", "bye", "quit"):
        return True
    if first == "monitor" and line.strip().lower().endswith("off"):
        return True
    return False


def guard(cfg: dict, line: str) -> tuple[bool, str]:
    """安全闸门。返回 (能不能发, 拒的理由)。只读命令永远过。"""
    if is_readonly(line):
        return True, ""
    first = line.strip().split(" ", 1)[0].lower()
    safe = cfg.get("安全") or {}
    if first in ("move", "movepx") and not safe.get("允许移动"):
        return False, "移动没开（配置.json 安全.允许移动=false）"
    if first in ("click", "mdown", "mup") and not safe.get("允许点击"):
        return False, "点击没开（配置.json 安全.允许点击=false）"
    if first in ("key", "combo", "text") and not safe.get("允许按键"):
        return False, "按键没开（配置.json 安全.允许按键=false）"
    return True, ""


def _parse_status(line: str) -> dict:
    """'OK v=18 mode=hid inj=1 up=57s btn=0 pxpc=2.500' → dict"""
    out = {"raw": line, "ok": line.startswith("OK")}
    for tok in line.split()[1:]:
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        if v.endswith("s") and k == "up":
            v = v[:-1]
        try:
            out[k] = float(v) if "." in v else int(v)
        except ValueError:
            out[k] = v
    return out


class Hand:
    """一次会话：连上 → 可以发多条 → 关的时候 release + bye。"""

    def __init__(self, host: str, port: int = 8300, timeout: float = 6.0, log=None):
        self.host, self.port, self.timeout = host, int(port), float(timeout)
        self.log = log
        self.sock = None
        self.state = "new"
        # ★ 只有真按下过才需要在关连接时 release（见 close 的注释）
        self._pressed = False

    # ── 生命周期 ──
    def open(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            self.state = "down"
            raise HandError("连不上 %s:%d（%s）—— 这台机器的代理没起？"
                            % (self.host, self.port, exc)) from exc
        self.sock.settimeout(self.timeout)
        self.state = "up"
        return self

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        """收尾：**只有真按下过才发 release**。

        ★ 2026-10-25 13:41 实测（这条代价很隐蔽）：
          原来每次关连接都发 `release`。而 `release` 本身是一次**真注入**，
          板子的 monitor 会把它的按钮事件上报，且事件**晚于代理的 250ms 自抑制窗**才被读到
          → 每次短连接（哪怕只是问一句 status）都会把「真人在动」这个标记点亮
          → 接下来 8 秒所有动作被 ERR yield 拒绝。
          现场表现：`events` 每 2 秒 +1~2，`human_age` 永远回不到 8 秒。
          所以：没按下过就不发 release —— 收尾是为了放掉**按住的**键，不是为了打个招呼。
        """
        if self.sock is None:
            return
        if self._pressed:
            try:
                self._raw("release")
            except Exception:
                pass
        try:
            self._raw("bye")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
        self.sock = None
        self.state = "closed"

    # ── 收发 ──
    def _raw(self, line: str) -> str:
        self.sock.sendall((line + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise HandError("对端断了 / 不回话（发的是 %r）" % line)
            buf += chunk
        return buf.split(b"\n", 1)[0].decode("utf-8", "replace")

    def cmd(self, line: str) -> str:
        """发一条，拿一行回执。ERR busy 单独抛，别的 ERR 原样返回给调用方判。"""
        if self.sock is None:
            self.open()
        reply = self._raw(line).strip()
        if self.log:
            self.log("[手 %s:%d] %-18s -> %s" % (self.host, self.port, line, reply))
        if reply.startswith("ERR busy"):
            raise HandBusy("板子被别的客户端占着（单客户端规矩）：%s" % reply)
        if line.strip().lower().startswith("mdown"):
            self._pressed = True
        elif line.strip().lower().startswith("mup") or line.strip().lower() == "release":
            self._pressed = False
        return reply

    # ── 语义封装 ──
    def status(self) -> dict:
        return _parse_status(self.cmd("status"))

    def diag(self) -> dict:
        line = self.cmd("diag")
        try:                                  # 'OK diag {...}'
            return json.loads(line.split("diag", 1)[1].strip())
        except Exception:
            return {"raw": line}


def ask(cfg: dict, node: str, line: str, log=None) -> dict:
    """一次性问一句就断（脑用这个）。返回 {ok, reply, ...}，永不抛给 HTTP 层。

    ★ 单客户端规矩的副产品（2026-10-25 实测）：板子只认一个客户端，
      **后连上来的会把前一个 RST 掉**（对端拿到 WinError 10054）。
      所以两个客户端同时摸同一块板，双方都会看到「时灵时不灵」。
      本模块的对策：
        1. 只读命令（status/release/diag）撞上 RST 就重试一次 —— 它们幂等；
        2. 会动东西的命令**绝不重试**（重试 = 动两次）。
    """
    ok, why = guard(cfg, line)
    if not ok:
        return {"ok": False, "node": node, "cmd": line, "code": "guarded", "why": why}
    try:
        host, port = node_addr(cfg, node)
    except HandError as exc:
        return {"ok": False, "node": node, "cmd": line, "code": "no-node", "why": str(exc)}

    tries = 2 if is_readonly(line) else 1
    last = None
    # ★ 2026-10-26 19:3x：`ERR busy` 也重试（最多 5 次，约 0.5 秒）。
    #   为什么可以重试：busy 是**连接被拒、命令根本没执行**，不是"执行了但结果不明"。
    #   实测症状：面板每 0.7 秒轮询状态，占着手的单客户端名额，
    #   于是连续动作全部失败、每条都回 busy —— 看起来像没生效，其实是没连上。
    忙重试 = 5
    for attempt in range(tries):
        for 忙次 in range(忙重试):
            try:
                with Hand(host, port, log=log) as h:
                    reply = h.cmd(line)
                good = reply.startswith("OK")
                return {"ok": good, "node": node, "cmd": line, "reply": reply,
                        **({} if good else {"code": "err", "why": reply})}
            except HandBusy as exc:
                last = {"ok": False, "node": node, "cmd": line, "code": "busy", "why": str(exc)}
                if 忙次 + 1 < 忙重试:
                    time.sleep(0.08 + 0.04 * 忙次)      # 80/120/160/200ms 退让
                continue
            except (ConnectionResetError, ConnectionAbortedError) as exc:
                last = {"ok": False, "node": node, "cmd": line, "code": "reset",
                        "why": "板子把这个连接 RST 了（%s）—— 通常是有第二个客户端在摸同一块板"
                               "（单客户端规矩：后连上来的会踢掉前一个）" % exc}
                break
            except HandError as exc:
                last = {"ok": False, "node": node, "cmd": line, "code": "down", "why": str(exc)}
                break
            except Exception as exc:           # 兜底：任何怪错都不许往上冒
                last = {"ok": False, "node": node, "cmd": line, "code": "error",
                        "why": "%s: %s" % (type(exc).__name__, exc)}
                break
        if attempt + 1 < tries:
            time.sleep(0.15)
    return last


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cfg = load_cfg()
    node = (sys.argv[1] if len(sys.argv) > 1 else "A").upper()
    rest = sys.argv[2:] or ["status"]
    print(json.dumps(ask(cfg, node, " ".join(rest)), ensure_ascii=False))
