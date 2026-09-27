# 安装与使用

## 环境

已测试 Ubuntu 24.04、Python 3.12、Codex CLI 0.155.1。本项目使用 Linux `/proc`、
systemd user、Unix socket 与 BlueZ，不是 Windows/macOS 安装包。

```bash
sudo apt update
sudo apt install python3-venv fonts-noto-cjk fonts-dejavu-core fonts-droid-fallback bluez
sudo usermod -aG dialout "$USER"
```

修改串口组后注销并重新登录。不要格式化已有 SD 卡，不要删除 Codex 会话或认证文件。

## 固件与 SD

1. 从 Release 下载 `codex-assistant-runtime.zip`、`codex-assistant-sd.zip` 和 `SHA256SUMS`。
2. 校验下载的 ZIP 对应 SHA-256，解压到长期保留的位置。包内 `MANIFEST.sha256` 可进一步校验逐文件内容。
3. 只对 JC4880P443C_I_W / ESP32-P4 rev 1.x 使用本固件。先核对 Flash 分区，并备份已有设备。
4. 给设备断电。把 SD 包中 `sd-card/` 的文件直接放在 FAT32 卡根目录，不能再套一层文件夹。
   应有 9 个 `C*.CJP`、27 个 `T*.CJP`、3 个 `A*.CJP` 以及清单。安全卸载读卡器，再插回开发板。

先安装运行环境：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r host/requirements.txt 'esptool==5.4.0'
cp host/settings.example.json host/settings.json
cp host/aliases.example.json host/aliases.json
```

烧录前停止已有桥接和串口监视器。以下命令在运行包根目录执行：

```bash
systemctl --user stop codex-pet-lifecycle.service
bash host/run-background.sh stop
.venv/bin/python -m esptool --chip esp32p4 --port /dev/ttyACM0 chip-id
```

不存在服务时 stop 报“未找到”可忽略，但端口占用不能忽略。**已使用本项目 4 MiB
应用分区的设备**只更新应用，不写 NVS：

```bash
.venv/bin/python -m esptool --chip esp32p4 --port /dev/ttyACM0 -b 460800 \
  write-flash 0x10000 firmware/codex_pet_p4.bin
```

**空白设备或不同分区布局**，先备份和确认硬件，再写完整布局：

```bash
.venv/bin/python -m esptool --chip esp32p4 --port /dev/ttyACM0 -b 460800 \
  write-flash --flash-mode dio --flash-freq 80m --flash-size 16MB \
  0x2000 firmware/bootloader/bootloader.bin \
  0x8000 firmware/partition_table/partition-table.bin \
  0x10000 firmware/codex_pet_p4.bin
```

不运行 `erase-flash`。若热启动出现 SD 初始化 `0x107`，保持插卡，完全断电至少 5 秒后再启动。

## 自动连接

确认本机 `codex login status` 正常，再执行：

```bash
bash host/run-background.sh start
.venv/bin/python host/install_service.py --dry-run
.venv/bin/python host/install_service.py --enable
```

安装器会显示/生成当前项目路径的用户服务，旧服务先备份。它每 2 秒检查 Codex
进程；最后一个 Codex 和桌面助手都退出后停止桥接。移动目录后需重新安装服务。
这次发布过程不会替发布者安装或重启这些服务。

USB 默认自动识别本板。首次连接保留 USB，让桥接识别、配对本板的 `Codex-P4`。
随后 USB 不可用时通过已配对 BLE 连接，USB 恢复握手后切回。请另行供电。
配对采用加密的 LE Secure Connections Just Works，无口令 MITM 认证；首次配对请在可信环境进行。

## 双向模型控制

普通独立 CLI 进程不能被桥接直接修改模型。使用本项目的共享 app-server 入口启动会话：

```bash
export CODEX_PET_REAL_CODEX="$(command -v codex)"
bash host/codex-device.sh
# 或恢复历史会话
bash host/codex-device.sh resume --all
```

`CODEX_PET_REAL_CODEX` 必须指向原始 Codex 可执行文件，不能指回项目包装入口。
默认入口仍兼容本机 npm 的 `~/.npm-global/bin/codex`；其他安装位置请显式设置上述变量。
若需要日常 `codex` 默认支持模型控制，可在 shell 配置中设置该变量的绝对路径，
再将 `host/codex_default.py` 链接到独立的 PATH 目录。不要覆盖 npm 原始文件。
该包装入口将 `exec`、`login`、帮助和显式远端调用原样转交；不启用默认 YOLO。
`CODEX_PET_BYPASS=1` 可临时绕过包装入口。

物理主题键短按切换主题；长按出现小进度环并打开模型侧栏，单击移动选择，长按确认。
按下时立即固定当前对话。确认后侧栏收回，执行中的任务完成后再应用。
Codex `/model` 与设备显示双向同步；可选模型以服务端实际返回为准。

## API 统计

效果菜单选择 `自动 / OpenAI / MoreCode`。这不是推理服务切换按钮。

- OpenAI：通过本机 Codex app-server 获取账号类型和可用的周额度。API Key 登录不提供会员周额度。
- MoreCode：当前适配 Microsoft Edge 默认 Profile 中 `https://api.morecode.top/console` 的登录态。
  本机同一用户登录网站即可；Cookie 过期、浏览器加密或钥匙串未解锁会导致查询失败。
- 查询最小间隔 2 秒，错误退避。Token/s 是日志采样增量，等待工具时为 0 并不意味着断线。
- 会员到期时间由用户设置。桌面窗口效果菜单可修改；仅运行桥接时，编辑 `host/settings.json`
  的 `membership_expires_at`，格式 `YYYY-MM-DDTHH:MM`，留空表示未知。设置会同步到 ESP32。
- `rotation_mode`：`off`、`active`、`all`，默认 `active`，间隔 5 秒。

## 可选桌面窗口

最小运行包不要求 Qt。需要同款桌面窗口时，下载源码包：

```bash
sudo apt install build-essential cmake ninja-build libxcb-cursor0 libxkbcommon-x11-0
.venv/bin/python -m pip install -r desktop/requirements.txt
.venv/bin/python tools/fetch_lvgl.py
bash desktop/build.sh
bash desktop/run.sh
```

`tools/fetch_lvgl.py` 只下载已固定版本的 LVGL 源码并校验，不需要完整 ESP-IDF。
桌面窗口可置顶、缩放、预览动效、设置周期与会员到期时间。最小桥接与桌面共用本机 IPC。

## 排障

| 现象 | 检查 |
| --- | --- |
| 未连接 | 串口权限、USB 数据线、是否并发占用、`bash host/run-background.sh logs` |
| BLE 不连接 | 先用 USB 完成本板配对，开启 BlueZ，检查独立供电和板载 C6 固件 |
| 思想者无画面 | FAT32 卡根目录文件、清单校验；没有卡时不使用 USB 传帧兜底 |
| 模型切换失败 | 通过共享入口启动、服务端可用模型、当前任务是否仍在执行 |
| 额度 `--` | 对应账号登录是否有效；未知不等于 0；到期时间不自动获取 |
| 周期改变无效果 | 补齐三个 `A*.CJP` 分层文件；没有分层时不会通过改变背景速度模拟 |

精确诊断时停止桥接后再开串口工具。不要为了排错先擦除 NVS、格式化 SD 或删除 Codex 历史。
