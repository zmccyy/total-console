# 总控台引入 psutil 作为唯一第三方依赖

Status: accepted

参考仓库 local-ops 坚持零第三方依赖（纯标准库 + npm 全局安装的 `claude` 等二进制均有先例），但本项目的进程/端口快照需要每 5s 轮询一次并计算 CPU 占用率。纯标准库路线在 Windows 上有两条代价不同的路径：PowerShell CIM 单快照（约 0.3–1s/次，WMI 偶发挂起会卡死整条扫描链）或 ctypes 直调 Win32 API（300+ 行易错 C 绑定）。本项目仅面向 Windows，零依赖买不到跨平台可移植性，而 psutil 本机已安装、单依赖即可同时覆盖进程、端口、CPU 与启动时间，故决定：**Python 标准库 + psutil（唯一第三方依赖）**，其余部分（HTTP 服务、前端）保持无依赖。

## Considered Options

- **psutil（采纳）**：一次 `process_iter` 快照 ~几十毫秒，自带 CPU%/端口/父进程；本机已装。
- **PowerShell CIM 单快照**：代码量最小，但 5s 一轮损耗 6–20% 单核，WMI 挂起是运维面板不可接受的单点风险。
- **ctypes 直调 Win32 API**:性能最优，但绑定代码量大、调试成本高，无必要。
- **参考仓库零依赖哲学全盘继承**：社区 PR #4（server_win.py）有此先例，但其场景为 2s 轮询且无 CPU% 需求，本项目的轮询频率与指标集不同。

## Consequences

- 所有系统采集代码收敛在 scanner 抽象层之后，若日后想恢复零依赖，仅需重写该层、接口不变。
- 安装步骤增加一行 `pip install psutil`；交付形态仍为脚本启动，不打包。
