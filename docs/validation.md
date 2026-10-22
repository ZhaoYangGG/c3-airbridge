# 验证记录

不要把模拟器回环、固件编译或蓝牙广播，写成 iPhone 端到端验证成功。

| 检查 | 结果 |
|---|---|
| 本机识别真实设备 | ESP32-C3 QFN32 rev v0.4，4 MB Flash，原生 USB Serial/JTAG |
| 原固件备份 | 已在仓库外保存 4,194,304 字节完整 Flash 和 SHA-256；不公开备份 |
| ESP-IDF 构建 | v6.0.1，目标 esp32c3，DIO/40 MHz，通过 |
| 后端测试 | 10 项通过：分片控制帧、plist、大小限制、鉴权、1 MiB 字节校验、通道上限、UDP 回程、端口映射 |
| 固件烧录/启动 | 实板写入校验通过；正常启动，BLE 广播启动，USB `status` 可读 |
| 未配网时剩余内存 | 约 161,968 字节 free heap、155,212 字节 minimum heap；不能当作视频负载内存指标 |
| UxPlay 后端 | 固定提交 `0477d471cc5899ea13382b5871cf70dbfe050ebd` 已在 Apple Silicon Mac 编译通过 |
| C3 网络实机回环 | 待配网验证 |
| iPhone 发现/握手 | 待实测 |
| 经 C3 接收持续画面 | 待实测 |
| iPhone BLE HID | 待实测 |
| 视频与 HID 同时运行 | 待实测 |
| Agent 自主操作闭环 | 未接入 |

## 验收顺序

1. 后端 echo 模式，真实 C3 上执行 1 MiB 和持续流传输，比较 SHA-256，记录最小剩余 heap。
2. UxPlay 直连作为对照；C3 广播真实 TXT 后，通过 C3 完成 AirPlay 连接。
3. 检查 SETUP 的时序、视频与音频端口，以及 C3→后端和后端→手机的字节计数。
4. 持续至少 5 分钟的屏幕变化，不将静态连接存活等同于画面持续更新。
5. 打开 AssistiveTouch，确认 move/click/key 的真实效果，再并发视频。
6. 接入截图适配器和 Agent，明确停止/释放输入机制后测试任务。

仅公开不含 Wi-Fi、token、手机截图、设备 MAC 或完整备份的测试结果。
