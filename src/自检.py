# -*- coding: utf-8 -*-
"""
深城修改器 · 自检
--------------------------------------------------------------------------
双击本文件（或用 python 运行）会依次检查:
  1. 能不能连上 MuMu / 找到游戏
  2. 静默 adb 是否正常（不该弹控制台窗口）
  3. 内存通道是否正常（快照 + 法术定位）
  4. Frida 智能通道是否正常（读存档字段 / 读货币 / 读资源 / 写回）
  5. 广告钩子能不能挂上

任何一步失败都会直接告诉你原因。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

PKG = 'com.rockbite.deeptown'


def hr(t):
    print('\n' + '=' * 62)
    print('  ' + t)
    print('=' * 62)


def main():
    from adbmem import Adb, guess_adb, MUMU_PORTS
    import engine as E

    hr('1. 连接模拟器 / 找游戏')
    adb = Adb(guess_adb())
    print('   adb:', adb.adb)
    for p in MUMU_PORTS:
        try:
            adb.connect('127.0.0.1', p)
        except Exception:
            pass
    devs = adb.devices()
    print('   设备:', devs)
    if not devs:
        print('   ✗ 没找到模拟器 —— 请先启动 MuMu')
        return 1
    adb.serial = devs[0]
    adb.root()
    pid = adb.pid_of(PKG)
    print('   游戏 pid:', pid)
    if not pid:
        print('   ✗ 游戏没运行 —— 请在模拟器里打开深城')
        return 1
    ver = adb.pkg_version(PKG)
    print('   版本: %s (vc %s)' % ver)

    hr('2. 静默 adb（这一步不该有任何窗口闪出来）')
    t = time.time()
    maps = adb.maps(pid)
    print('   maps 区域 %d 个，耗时 %.2fs' % (len(maps), time.time() - t))
    print('   （如果你刚才看到控制台窗口闪，说明 adbmem.py 的 hidden_kw 没生效）')

    hr('3. 内存通道')
    loc = E.Locator(adb, pid)
    t = time.time()
    snap = loc.take_snapshot()
    print('   快照 %.0f MB，耗时 %.1fs' % (snap.total() / 1048576, time.time() - t))

    hr('4. Frida 智能通道')
    try:
        import frida
        print('   frida 模块版本:', frida.__version__)
        if not frida.__version__.startswith('16.'):
            print('   ✗ 需要 16.x：  pip install "frida==16.7.19"')
            return 1
    except Exception as e:
        print('   ✗ 没装 frida:', e)
        return 1

    import frida_client as FC
    ch = FC.FridaChannel(adb.adb, adb.serial)
    if not ch.ensure_server(print):
        return 1
    if not ch.attach(pid, print):
        return 1
    api = ch.api
    print('   ping:', api.ping())

    rows = api.dump() or []
    print('   SaveData 字段 %d 个' % len(rows))
    print('   currentLevel =', api.get('currentLevel'))
    print('   cash         =', api.currency('cash'))
    print('   crystals     =', api.currency('crystals'))
    mats = api.materials() or []
    print('   资源(前 8)   =', [(m['key'], m['value']) for m in mats[:8]])

    before = api.get('currentLevel')
    api.set('currentLevel', 42)
    mid = api.get('currentLevel')
    api.set('currentLevel', before)
    print('   写入自检: currentLevel %s -> %s -> %s %s'
          % (before, mid, api.get('currentLevel'), '✓' if mid == 42 else '✗'))

    hr('5. 广告钩子')
    print('   install:', api.adinstall({'blockInterstitial': True,
                                       'skipRewarded': True, 'forceReady': True}))
    time.sleep(1)
    print('   status :', api.adstatus())

    hr('6. 游戏变速')
    st = api.setspeed(1.0)
    time.sleep(1.5)
    st = api.speedstatus()
    print('   钩子:', st.get('hooked'), ' 方式:', st.get('mode'), ' 目标:', st.get('fields'))
    print('   ECS 时间片: %s -> %s' % (st.get('rawDt'), st.get('scaledDt')))
    if st.get('err'):
        print('   ✗', st['err'])
    else:
        api.setspeed(10.0)
        time.sleep(2)
        s2 = api.speedstatus()
        print('   10x 后 ECS 时间片: %s -> %s' % (s2.get('rawDt'), s2.get('scaledDt')))
        print('   已改帧数:', s2.get('calls'), ' 方式:', s2.get('mode'))
        print('   （10x 时 scaledDt 应约为 rawDt 的 10 倍；这就是交给游戏逻辑的每帧时间片）')
        api.setspeed(1.0)
        print('   已还原 1x')

    hr('7. 倒计时（制作 / 远征 / 部署… 只读，不会动你的存档）')
    try:
        d = api.timerslist(None)
        ts = sorted(d.get('tasks') or [], key=lambda t: t['remaining'])
        print('   调度器里共 %d 个倒计时（全部是墙钟计时，全局变速对它们无效）:' % len(ts))
        for t in ts[:14]:
            print('     %-12s %-44s %10.1f 秒' % (t['kind'], t['key'][-44:], t['remaining']))
        if len(ts) > 14:
            print('     … 还有 %d 个' % (len(ts) - 14))
        for s in (d.get('scripts') or []):
            print('   远征建筑 %s  洞穴=%s  状态=%s' % (s.get('cls', '').split('.')[-1],
                                                    s.get('caveTitle'), s.get('state')))
        if d.get('err'):
            print('   ⚠', d['err'])
        else:
            print('   ✓ 倒计时通道正常（「立即完成」= a7.b.r(key,0)，把 endTime 挪到当前墙钟）')
    except Exception as e:
        print('   ✗ 倒计时接口失败:', e)

    hr('8. 机制杀助手（只读检测，不会动你的存档）')
    try:
        d = api.mechscan()
        if not d.get('found'):
            print('   ⚠', d.get('err') or '读不到当前街区')
        else:
            mech = d.get('mech') or {}
            print('   当前街区: 段 %s / 行 %s   血量 %s' % (d.get('seg'), d.get('row'), d.get('maxHp')))
            print('   类型    : %s（%s / getType()=%s）' % (d.get('name'), d.get('cls'), d.get('type')))
            print('   免伤系数: getHitMod() = %s' % d.get('hitMod'))
            print('   免疫表  : %s' % (d.get('immunity') or '（空）'))
            print('   判定    : %s' % d.get('verdict'))
            if mech:
                print('   机制    : %s —— %s' % (mech.get('name'), mech.get('need')))
            if d.get('flags'):
                f = d['flags']
                print('   BOSS标志: M=%s O=%s P(已击败)=%s R=%s'
                      % (f.get('M'), f.get('O'), f.get('P'), f.get('R')))
            eg = d.get('endgame')
            if eg:
                print('   终局前置: 发射剧情标记 = %s' % eg.get('movieDone'))
                for b in eg.get('buildings') or []:
                    print('     %s %-6s 建筑=%s  材料 %s=%s'
                          % ('OK' if b.get('built') else '缺', b.get('cn'),
                             b.get('built') or 0, b.get('matCn'), b.get('matValue'))
                          if False else
                          '     %s %-6s 建筑=%s  材料 %s=%s'
                          % ('OK' if b.get('built') else '缺', b.get('cn'),
                             b.get('built') or 0, b.get('matCn'),
                             '—' if b.get('matHave') is None else b.get('matHave')))
                print('     ⇒ %s' % ('三栋齐了，走发射剧情' if eg.get('ready')
                                      else '还缺 %d 项' % len(eg.get('missing') or [])))
            print('   ✓ 机制检测通道正常'
                  '（LavaBlock→freeze()；铁长老→只报终局前置清单+补材料，不装钩子；强制旁路需在面板里显式点）')
    except Exception as e:
        print('   ✗ 机制检测接口失败:', e)

    ch.detach()
    hr('全部检查完成')
    print('  回游戏点一下"看广告"的按钮试试 —— 应该直接发奖励。')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        print('\n✗ 自检中断（上面是详细堆栈）')
        sys.exit(1)
