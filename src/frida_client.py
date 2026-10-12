# -*- coding: utf-8 -*-
"""
深城修改器 · Frida 通道
--------------------------------------------------------------------------
负责把 frida-server 弄起来、附加上游戏、加载 agent.js, 并对外提供一个
简单的 Python API(script.exports_sync)。

依赖: frida 16.x 的 Python 包(17.x 把 Java 桥接拆出去了, 用不了)。
      frida-server 二进制放在 ./bin/frida-server-x86_64, 首次会自动推送到设备。
"""
import os
import sys
import time
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_JS = os.path.join(HERE, 'agent.js')
BIN_DIR = os.path.join(HERE, 'bin')
PKG = 'com.rockbite.deeptown'
PORT = 27042
DEV_BIN = '/data/local/tmp/frida-server'

CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


def _run_kw():
    """Windows 下不弹控制台窗口"""
    if os.name != 'nt':
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return {'creationflags': CREATE_NO_WINDOW, 'startupinfo': si}


def frida_version():
    try:
        import frida
        return frida.__version__
    except Exception as e:
        return 'ERR:' + str(e)


class FridaChannel:
    """一条 frida 通道: 确保 server -> 附加 -> 加载 agent"""

    def __init__(self, adb_path=None, serial=None):
        self.adb = adb_path
        self.serial = serial
        self.device = None
        self.session = None
        self.script = None
        self.api = None
        self.pid = None

    # ---------------- adb ----------------
    def _adb(self, *args, timeout=30):
        base = [self.adb]
        if self.serial:
            base += ['-s', self.serial]
        try:
            return subprocess.run(base + list(args), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=timeout,
                                  encoding='utf-8', errors='replace', **_run_kw())
        except Exception as e:
            class R:
                stdout = 'ERR %s' % e
                returncode = 1
            return R()

    def shell(self, cmd, timeout=30):
        return self._adb('shell', cmd, timeout=timeout).stdout or ''

    # ---------------- frida-server ----------------
    def server_running(self):
        return 'frida-server' in self.shell('ps -A -o NAME | grep frida-server')

    def ensure_server(self, log=print):
        if self.server_running():
            log('frida-server 已在运行')
            return True
        log('正在启动 frida-server …')
        # 设备上有没有
        ls = self.shell('ls -la %s 2>/dev/null' % DEV_BIN)
        if 'frida-server' not in ls:
            local = None
            for c in (os.path.join(BIN_DIR, 'frida-server-x86_64'),
                      os.path.join(BIN_DIR, 'frida-server')):
                if os.path.exists(c):
                    local = c
                    break
            if not local:
                log('✗ 找不到 frida-server 二进制（%s 目录下应有 frida-server-x86_64）' % BIN_DIR)
                return False
            log('推送 frida-server 到设备（约 110MB，稍等）…')
            r = self._adb('push', local, DEV_BIN, timeout=600)
            if 'error' in (r.stdout or '').lower():
                log('✗ 推送失败：' + r.stdout.strip()[:200])
                return False
        self.shell('chmod 755 %s' % DEV_BIN)
        self.shell('setsid %s >/dev/null 2>&1 < /dev/null &' % DEV_BIN)
        for _ in range(20):
            time.sleep(0.5)
            if self.server_running():
                log('✓ frida-server 已启动')
                return True
        log('✗ frida-server 启动失败（需要 root / 模拟器已开启 root）')
        return False

    # ---------------- 连接 ----------------
    def attach(self, pid=None, log=print):
        try:
            import frida
        except Exception as e:
            log('✗ 没有安装 frida 模块：%s' % e)
            return False
        ver = frida.__version__
        if not ver.startswith('16.'):
            log('⚠ 当前 frida 是 %s；本工具需要 16.x（17 起 Java 桥接被拆出，脚本用不了）' % ver)
            log('  修复：pip install "frida==16.7.19"')
            return False

        self._adb('forward', 'tcp:%d' % PORT, 'tcp:%d' % PORT)
        if pid is None:
            pid = self.shell('pidof %s' % PKG).strip().split()
            pid = int(pid[0]) if pid else None
        if not pid:
            log('✗ 游戏没在运行，请先在模拟器里打开深城')
            return False
        self.pid = pid

        try:
            self.device = frida.get_device_manager().add_remote_device('127.0.0.1:%d' % PORT)
            self.session = self.device.attach(pid)
            src = open(AGENT_JS, encoding='utf-8').read()
            self.script = self.session.create_script(src)
            self.script.on('message', lambda m, d: log('[agent] %s' % (
                m.get('payload') if m.get('type') == 'send' else m)))
            self.script.load()
            self.api = self.script.exports_sync
            log('✓ 已附加到游戏进程 (pid %d)' % pid)
            return True
        except Exception as e:
            log('✗ 附加失败：%s' % e)
            return False

    def detach(self):
        try:
            if self.session:
                self.session.detach()
        except Exception:
            pass
        self.session = self.script = self.api = None


def check_env(log=print):
    """给 GUI 用的环境自检"""
    v = frida_version()
    ok = v.startswith('16.')
    log('frida 模块：%s %s' % (v, '✓' if ok else '✗ 需要 16.x'))
    return ok
