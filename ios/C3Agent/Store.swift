import Foundation
import Security

enum AgentError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}

@MainActor final class Settings {
    var baseURL: String { get { UserDefaults.standard.string(forKey: "baseURL") ?? "https://dashscope.aliyuncs.com/compatible-mode/v1" } set { UserDefaults.standard.set(newValue, forKey: "baseURL") } }
    var model: String { get { UserDefaults.standard.string(forKey: "model") ?? "qwen3.5-plus" } set { UserDefaults.standard.set(newValue, forKey: "model") } }
    var decisionBaseURL: String { get { UserDefaults.standard.string(forKey: "decisionBaseURL") ?? "https://open.bigmodel.cn/api/paas/v4" } set { UserDefaults.standard.set(newValue, forKey: "decisionBaseURL") } }
    var decisionModel: String { get { UserDefaults.standard.string(forKey: "decisionModel") ?? "autoglm-phone" } set { UserDefaults.standard.set(newValue, forKey: "decisionModel") } }
    // Retain the old key/account as the auxiliary vision profile. Never send
    // a saved key to a different provider merely because the default changed.
    var decisionAPIKey: String {
        get { readKey("c3-agent-decision-key") }
        set { writeKey("c3-agent-decision-key", newValue) }
    }
    private let account = "c3-agent-model-key"
    var apiKey: String {
        get { readKey(account) }
        set { writeKey(account, newValue) }
    }
    private func readKey(_ account: String) -> String {
            let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrAccount as String: account, kSecReturnData as String: true]
            var result: CFTypeRef?
            guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return "" }
            return String(data: data, encoding: .utf8) ?? ""
    }
    private func writeKey(_ account: String, _ newValue: String) {
            let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrAccount as String: account]
            SecItemDelete(query as CFDictionary)
            if !newValue.isEmpty {
                var item = query
                item[kSecValueData as String] = Data(newValue.utf8)
                item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
                SecItemAdd(item as CFDictionary, nil)
            }
    }
    func importBootstrap() {
        let url = PrivateFiles.root.appendingPathComponent("bootstrap.json")
        guard let data = try? Data(contentsOf: url), let obj = try? JSONSerialization.jsonObject(with: data) as? [String: String] else { return }
        if let key = obj["api_key"], !key.isEmpty { apiKey = key }
        if let base = obj["base_url"] { baseURL = base }
        if let name = obj["model"] { model = name }
        if let key = obj["decision_api_key"], !key.isEmpty { decisionAPIKey = key }
        if let base = obj["decision_base_url"] { decisionBaseURL = base }
        if let name = obj["decision_model"] { decisionModel = name }
        try? FileManager.default.removeItem(at: url)
    }
}

enum PrivateFiles {
    static let root = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    static func json(_ name: String, _ value: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]) else { return }
        try? data.write(to: root.appendingPathComponent(name), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }
}
