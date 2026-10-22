# 验证记录

不要把模拟器回环、固件编译或蓝牙广播，写成 iPhone 端到端验证成功。

| 检查 | 结果 |
|---|---|
| 本机识别真实设备 | ESP32-C3 QFN32 rev v0.4，4 MB Flash，原生 USB Serial/JTAG |
| 原固件备份 | 已在仓库外保存 4,194,304 字节完整 Flash 和 SHA-256；不公开备份 |
| ESP-IDF 构建 | v6.0.1，目标 esp32c3，DIO/40 MHz，通过 |
| 后端测试 | 13 项通过：分片控制帧、plist、大小限制、鉴权、1 MiB 字节校验、通道上限、UDP 回程、端口映射、关闭确认、设备重连、截图鉴权/过期拒绝 |
| 固件烧录/启动 | 实板写入校验通过；正常启动，BLE 广播启动，USB `status` 可读 |
| 未配网时剩余内存 | 约 161,968 字节 free heap、155,212 字节 minimum heap；不能当作视频负载内存指标 |
| UxPlay 后端 | 固定提交 `0477d471cc5899ea13382b5871cf70dbfe050ebd` 已在 Apple Silicon Mac 编译通过 |
| C3 网络实机回环 | 1 MiB 数据 SHA-256 一致；连续双向回环用时 16.830 秒，合计双向有效吞吐约 0.997 Mbit/s；该结果不等同于视频帧率 |
| C3 连续回环内存 | 本轮状态上报约 70,356 字节 free heap、57,984 字节 minimum heap；BLE 尚未连接 |
| AirPlay 控制经真实 C3 | 连接 C3 的 7000 端口，后端 UxPlay 返回 `/info`：RTSP 200，891 字节完整可解析 plist |
| AirPlay 服务发现 | Mac 解析 C3 的 `_airplay._tcp` 服务，确认返回 C3 的实际 IPv4、端口 7000 及接收器 TXT 字段 |
| GitHub CI | 初始提交后端检查与 ESP32-C3 固件构建均通过 |
| iPhone 发现/握手 | iPhone 15 Pro Max 实机成功；Mac Bonjour 入口隔离后经 C3 完成 SETUP、时序回程和视频连接 |
| 经 C3 接收持续画面 | 已收到真实画面，连续多分钟累计上行超过 6 MB；三个活动通道，UxPlay 控制/视频连接均来自 loopback 代理；JPEG 内容持续变化，抽样新鲜度约 0.03–1.4 秒；实收画面 720×1560，客户端未采用请求的 480×1040 |
| iPhone BLE HID | 已连接并订阅鼠标/键盘；最初发送成功但未观察到屏幕响应，不能算输入验收；正在验证主动安全配对修复 |
| 视频与 HID 同时运行 | 待实测 |
| Agent 自主操作闭环 | 未接入 |

## 验收顺序

1. 后端 echo 模式，真实 C3 上执行 1 MiB 和持续流传输，比较 SHA-256，记录最小剩余 heap。使用 `hardware_probe.py --stream` 测连续流；默认模式逐块等待往返，不代表单向吞吐极限。
2. UxPlay 直连作为对照；C3 广播真实 TXT 后，通过 C3 完成 AirPlay 连接。
3. 检查 SETUP 的时序、视频与音频端口，以及 C3→后端和后端→手机的字节计数。
4. 持续至少 5 分钟的屏幕变化，不将静态连接存活等同于画面持续更新。
5. 打开 AssistiveTouch，确认 move/click/key 的真实效果，再并发视频。
6. 接入截图适配器和 Agent，明确停止/释放输入机制后测试任务。

仅公开不含 Wi-Fi、token、手机截图、设备 MAC 或完整备份的测试结果。

## macOS 发现冲突

首轮实际收到的画面曾绕过 C3：iPhone 将相同身份的 upstream 和 C3
广播合并，直接经 IPv6 连接 Mac。当时 C3 通道为零、字节计数不增长。
这次对照不计入 C3 视频成功。修复后 upstream 通过本机 Bonjour LocalOnly
发布，发现脚本用原生 DNS-SD 读取真实 TXT，再交给 C3 广播；随后 C3 上行
持续增长，UxPlay 的两个 TCP 连接对端均为 127.0.0.1。

BLE 配对验收必须检查安全会话和真实画面响应。普通 GATT 连接、订阅成功或
`POST /hid` 返回 `ok` 都不足以证明 iPhone 已接受鼠标键盘。
