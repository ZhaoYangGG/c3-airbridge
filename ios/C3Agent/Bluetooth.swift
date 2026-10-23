import Foundation
import CoreBluetooth

@MainActor final class Bluetooth: NSObject, @preconcurrency CBCentralManagerDelegate, @preconcurrency CBPeripheralDelegate {
    static let service = CBUUID(string: "C3331AD1-A52D-4AA2-B342-D75D708EA001")
    static let command = CBUUID(string: "C3331AD1-A52D-4AA2-B342-D75D708EA002")
    static let result = CBUUID(string: "C3331AD1-A52D-4AA2-B342-D75D708EA003")
    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var writeCharacteristic: CBCharacteristic?
    private var pending: (id: Int, continuation: CheckedContinuation<Void, Error>)?
    private var nextID = 0
    private var retry: Task<Void, Never>?
    var changed: (() -> Void)?
    private(set) var ready = false
    private(set) var status = "等待蓝牙"
    private(set) var acknowledged = 0
    private(set) var secure = false

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: .main,
            options: [CBCentralManagerOptionRestoreIdentifierKey: "c3-agent-central", CBCentralManagerOptionShowPowerAlertKey: true])
    }
    private func update(_ message: String) { status = message; changed?() }
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        if central.state == .poweredOn { connect() }
        else { reset(AgentError.message("蓝牙不可用")); update("请开启蓝牙") }
    }
    func centralManager(_ central: CBCentralManager, willRestoreState dict: [String: Any]) {
        if let found = (dict[CBCentralManagerRestoredStatePeripheralsKey] as? [CBPeripheral])?.first {
            use(found)
        }
    }
    func connect() {
        guard central.state == .poweredOn, !ready else { return }
        if let p = peripheral, p.state == .connecting { return }
        var candidates = central.retrieveConnectedPeripherals(withServices: [Self.service])
        if candidates.isEmpty {
            candidates = central.retrieveConnectedPeripherals(withServices: [CBUUID(string: "1812")]).filter {
                let name = ($0.name ?? "").lowercased()
                return name.contains("c3") || name.contains("airbridge") || name == "nimble"
            }
        }
        if candidates.isEmpty, let text = UserDefaults.standard.string(forKey: "c3Peripheral"), let id = UUID(uuidString: text) {
            candidates = central.retrievePeripherals(withIdentifiers: [id])
        }
        if let p = candidates.first { use(p) }
        else {
            update("搜索 C3 Agent…")
            central.scanForPeripherals(withServices: [Self.service], options: nil)
        }
    }
    private func use(_ p: CBPeripheral) {
        peripheral = p; p.delegate = self
        if p.state == .connected { p.discoverServices([Self.service]) }
        else { update("连接 C3…"); central.connect(p, options: nil) }
    }
    func centralManager(_ central: CBCentralManager, didDiscover p: CBPeripheral, advertisementData: [String: Any], rssi RSSI: NSNumber) {
        guard peripheral == nil || peripheral?.state == .disconnected else { return }
        central.stopScan(); use(p)
    }
    func centralManager(_ central: CBCentralManager, didConnect p: CBPeripheral) {
        UserDefaults.standard.set(p.identifier.uuidString, forKey: "c3Peripheral")
        p.discoverServices([Self.service]); update("发现指令服务…")
    }
    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        guard error == nil, let service = p.services?.first(where: { $0.uuid == Self.service }) else {
            update("未发现新版指令服务，请刷新蓝牙配对缓存"); return
        }
        p.discoverCharacteristics([Self.command, Self.result], for: service)
    }
    func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard error == nil else { update("发现指令特征失败"); return }
        writeCharacteristic = service.characteristics?.first { $0.uuid == Self.command }
        if let result = service.characteristics?.first(where: { $0.uuid == Self.result }) { p.setNotifyValue(true, for: result) }
    }
    func peripheral(_ p: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        guard characteristic.uuid == Self.result else { return }
        guard error == nil, characteristic.isNotifying, writeCharacteristic != nil else { update("订阅 C3 回执失败"); return }
        ready = true
        Task { @MainActor in
            do { try await self.send(["op": "ping"]); self.update("C3 已连接 · 安全蓝牙") }
            catch { self.ready = false; self.update(error.localizedDescription) }
        }
    }
    func peripheral(_ p: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard characteristic.uuid == Self.result, error == nil, let data = characteristic.value,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = obj["id"] as? Int, let wait = pending, wait.id == id else { return }
        pending = nil
        secure = obj["secure"] as? Bool == true
        if obj["ok"] as? Bool == true && secure { acknowledged += 1; wait.continuation.resume() }
        else { wait.continuation.resume(throwing: AgentError.message("C3 拒绝输入，请确认安全蓝牙配对")) }
        changed?()
    }
    func peripheral(_ p: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        if let error { failPending(error) }
    }
    private func failPending(_ error: Error) {
        if let wait = pending { pending = nil; wait.continuation.resume(throwing: error) }
    }
    private func reset(_ error: Error) {
        ready = false; secure = false; writeCharacteristic = nil; failPending(error)
    }
    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral p: CBPeripheral, error: Error?) {
        reset(AgentError.message("C3 已断开，任务输入已停止")); update("C3 已断开，正在重连…")
        retry?.cancel()
        retry = Task { @MainActor in try? await Task.sleep(for: .seconds(2)); if !Task.isCancelled { self.connect() } }
    }
    func centralManager(_ central: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        reset(AgentError.message("C3 连接失败")); peripheral = nil; connect()
    }
    func send(_ command: [String: Any]) async throws {
        try Task.checkCancellation()
        guard ready, let p = peripheral, p.state == .connected, let characteristic = writeCharacteristic else { throw AgentError.message("C3 指令通道未就绪") }
        guard pending == nil else { throw AgentError.message("已有指令等待回执") }
        nextID = nextID % 2_000_000_000 + 1
        let id = nextID
        var obj = command; obj["id"] = id
        let data = try JSONSerialization.data(withJSONObject: obj)
        guard data.count < 256, data.count <= p.maximumWriteValueLength(for: .withResponse) else { throw AgentError.message("指令长度超过蓝牙 MTU") }
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            pending = (id, continuation)
            p.writeValue(data, for: characteristic, type: .withResponse)
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(4))
                if self.pending?.id == id { self.failPending(AgentError.message("C3 指令回执超时，未自动重发")) }
            }
        }
    }
}
