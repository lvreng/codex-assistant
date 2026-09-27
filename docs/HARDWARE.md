# 硬件与构建

目标板：JC4880P443C_I_W，ESP32-P4 rev 1.x，16 MB Flash，PSRAM，ST7701 MIPI-DSI 屏与 GT911 触摸。
原生 480×800，横屏 UI 800×480；思想者画面 560×416。不要直接烧录至其他芯片修订版或屏幕。

| 接口 | 本项目配置 |
| --- | --- |
| 主题/模型按键 | GPIO35，低有效，原有物理键 |
| 背光 / LCD reset | GPIO23 / GPIO5 |
| 触摸 SDA / SCL | GPIO7 / GPIO8 |
| SD 卡 | SDMMC slot 0 |
| 板载 ESP32-C6 | SDIO slot 1；CMD19、CLK18、D0..D3 为 14..17、reset54 |
| Bluetooth | P4 NimBLE + ESP-Hosted 2.12.13 + 板载 C6，并非 P4 自带无线 |

请以 `main/board_display.c`、`main/sd_media_player.cpp` 与 `sdkconfig` 为完整定义。
本次不分发厂家手册全集，也不重刷 C6。现有板载 C6 报告的协处理器版本为 2.3.2；
不能保证任意其他 C6 出厂固件可用。

## 固件构建

已验证 ESP-IDF v6.1，具体工具链提交与固件哈希见 Release 中 `firmware/BUILD_INFO.json`。
安装并激活 Espressif 的 ESP-IDF 工具链后，在仓库根目录：

```bash
idf.py build
```

保留 `sdkconfig`、`sdkconfig.defaults` 与 `partitions.csv`。依赖锁定在 `dependencies.lock`，
组件管理器会联网下载 LVGL、ESP-Hosted 和触摸驱动等依赖。不需要克隆完整厂家 Demo。

`components/esp_lvgl_port` 保留本项目使用的本地显示修补及原许可证；
`components/esp_lcd_st7701` 是构建必需的驱动。不要无意替换为未经验证的上游版本。
主工程和运行包不包含 IDF、编译器、原机器路径、构建缓存或 NVS 内容。

## 本地播放器

Release 的 SD 素材直接复制原始文件，不重新编码：

- 9 段 `C00..C08.CJP` 循环，Sol、Terra、Luna、Astra 的工作/完成状态及 Astra 两种风格。
- 27 段 `Txyz.CJP` 目标状态锚定转场，覆盖 12 条有向模型切换路线。
- 3 段 `A00/A06/A07.CJP`：独立星云背景、经典星光、连接星光。
- 转场末帧 JPEG 与目标循环首帧一致；不是另插淡入动画来遮盖跳变。
- 周期只调整星光/连线，星云背景保持自己的时钟。

P4 PIE SIMD 对 RGB565 进行精确合成；PPA 旋转配合退休帧缓冲交接与脏区更新。
稳态 30 FPS 是指定板卡/素材的测量值，不代表所有 SD 卡和转场都锁定 30 FPS。
网页 GIF 的帧率仅用于浏览，不是硬件测速证据。

## 离线测试

```bash
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest host/test_protocol.py host/test_media_format.py \
  host/test_ble_transport.py host/test_bluez_pairing.py host/test_link_fallback.py \
  host/test_settings.py host/test_model_control.py -q
ctest --test-dir build/desktop --output-on-failure
```

`ctest` 需要先构建可选桌面库。硬件 smoke 工具会占用串口，不能与正常桥接并行运行。
