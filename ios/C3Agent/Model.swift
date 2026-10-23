import Foundation

@MainActor final class Model {
    let settings: Settings
    private(set) var calls = 0
    init(_ settings: Settings) { self.settings = settings }
    func ask(_ messages: [[String: Any]], maxTokens: Int = 800) async throws -> [String: Any] {
        guard !settings.apiKey.isEmpty else { throw AgentError.message("请先配置模型 API Key") }
        guard let base = URL(string: settings.baseURL), base.scheme == "https", base.host != nil,
              base.user == nil, base.password == nil, base.query == nil else { throw AgentError.message("模型地址必须为 HTTPS") }
        let url = base.appendingPathComponent("chat/completions")
        var request = URLRequest(url: url); request.httpMethod = "POST"; request.timeoutInterval = 60
        request.setValue("Bearer " + settings.apiKey, forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: Any] = ["model": settings.model, "messages": messages,
                                  "max_tokens": maxTokens, "temperature": 0.1,
                                  "response_format": ["type": "json_object"]]
        if base.host?.hasSuffix("aliyuncs.com") == true { body["enable_thinking"] = false }
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw AgentError.message("模型请求失败 HTTP \((response as? HTTPURLResponse)?.statusCode ?? 0)")
        }
        let root = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let choice = (root?["choices"] as? [[String: Any]])?.first,
              let message = choice["message"] as? [String: Any], let raw = message["content"] as? String else { throw AgentError.message("模型返回格式不正确") }
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("```") { text = text.components(separatedBy: "\n").dropFirst().dropLast().joined(separator: "\n") }
        guard let obj = try JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any] else { throw AgentError.message("模型没有返回有效动作") }
        calls += 1
        return obj
    }
    static func image(_ jpeg: Data) -> [String: Any] {
        ["type": "image_url", "image_url": ["url": "data:image/jpeg;base64," + jpeg.base64EncodedString()]]
    }
}

enum Actions {
    static let keys: [String: Int] = {
        var map = ["ENTER": 40, "ESC": 41, "BACKSPACE": 42, "TAB": 43, "SPACE": 44,
                   "MINUS": 45, "EQUAL": 46, "PLUS": 46, "SLASH": 56, "PERIOD": 55,
                   "RIGHT": 79, "LEFT": 80, "DOWN": 81, "UP": 82, "0": 39]
        for i in 0..<26 { map[String(UnicodeScalar(65 + i)!)] = 4 + i }
        for i in 1...9 { map[String(i)] = 29 + i }
        return map
    }()
    static func key(_ name: String, _ modifiers: [String] = []) throws -> [String: Any] {
        if name.uppercased() == "SEARCH" {
            guard modifiers.isEmpty else {throw AgentError.message("搜索按键不接受修饰键")}
            return ["op":"consumer","usage":0x221]
        }
        guard let usage = keys[name.uppercased()] else { throw AgentError.message("不支持的键盘按键") }
        let mods = ["CTRL": 1, "SHIFT": 2, "ALT": 4, "CMD": 8]
        var value = name.uppercased() == "PLUS" ? 2 : 0
        for mod in modifiers { guard let bit = mods[mod] else { throw AgentError.message("不支持的修饰键") }; value |= bit }
        return ["op": "key", "usage": usage, "modifiers": value]
    }
    static func text(_ text: String) throws -> [[String: Any]] {
        guard !text.isEmpty, text.count <= 160 else { throw AgentError.message("文本长度须为 1–160") }
        let punctuation: [Character: (Int, Int)] = [" ": (44,0), "+": (46,2), "-": (45,0), "=": (46,0), ".": (55,0), "/": (56,0),
            "@": (31,2), "_": (45,2), "?": (56,2), "!": (30,2), ":": (51,2), "'": (52,0), ",": (54,0)]
        return try text.map { char in
            guard let ascii = char.asciiValue else { throw AgentError.message("键盘文本请使用英文或拼音，并从画面选择中文候选") }
            if (65...90).contains(ascii) || (97...122).contains(ascii) { return try key(String(char).uppercased(), (65...90).contains(ascii) ? ["SHIFT"] : []) }
            if (48...57).contains(ascii) { return try key(String(char)) }
            if let (usage, mod) = punctuation[char] { return ["op": "key", "usage": usage, "modifiers": mod] }
            throw AgentError.message("不支持的键盘字符；回车必须单独下发")
        }
    }
    static func validate(_ obj: [String: Any]) throws {
        func number(_ name: String, _ range: ClosedRange<Double>) throws {
            guard let value = obj[name] as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(), range.contains(value.doubleValue), value.doubleValue.isFinite else { throw AgentError.message("模型动作参数无效: \(name)") }
        }
        switch obj["action"] as? String {
        case "click": try number("x", 0...1); try number("y", 0...1)
        case "swipe": for name in ["x", "y", "end_x", "end_y"] { try number(name, 0...1) }
        case "key":
            guard obj["modifiers"] == nil || obj["modifiers"] is [String] else { throw AgentError.message("修饰键须为字符串数组") }
            _ = try key(obj["key"] as? String ?? "", obj["modifiers"] as? [String] ?? [])
        case "text": _ = try text(obj["text"] as? String ?? "")
        case "scroll": try number("wheel", -10...10); guard (obj["wheel"] as! NSNumber).doubleValue.rounded() == (obj["wheel"] as! NSNumber).doubleValue else { throw AgentError.message("滚轮须为整数") }
        case "wait": try number("seconds", 0...5)
        case "done", "blocked": break
        default: throw AgentError.message("不支持的模型动作")
        }
    }
}
