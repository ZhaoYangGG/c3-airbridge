import Foundation

@main struct AutoGLMTests {
    @MainActor static func main() throws {
        var checks = 0
        func reject(_ block: () throws -> Void) {
            do { try block(); fatalError("Expected rejection") } catch { checks += 1 }
        }
        func action(_ text: String) throws -> [String: Any] { try PhoneDecision.parse(text).c3Action() }
        let tap = try action("<think>Tap the visible result.</think><answer>do(action=\"Tap\", element=[250,750])</answer>")
        precondition(tap["action"] as? String == "click" && tap["x"] as? Double == 0.25 && tap["y"] as? Double == 0.75); checks += 1
        let swipe = try action("do(action='Swipe', start=[0,500], end=[1000,500])")
        precondition(swipe["x"] as? Double == 0 && swipe["end_x"] as? Double == 1); checks += 1
        let native = try action("A brief explanation\ndo(action=\"Type\", text=\"hello, world!\")")
        precondition(native["text"] as? String == "hello, world!"); checks += 1
        let escaped = try action(#"do(action="Type", text="it\'s")"#)
        precondition(escaped["text"] as? String == "it's"); checks += 1
        let finished = try action("finish(message=\"Visible result is 46\")")
        precondition(finished["action"] as? String == "done"); checks += 1
        let launch = try action("do(action=\"Launch\", app=\"微信\")")
        precondition(launch["key"] as? String == "SEARCH" && (launch["execution_note"] as? String)?.contains("NOT complete") == true); checks += 1
        let blocked = try action("do(action=\"Tap\", element=[500,500], message=\"Confirm payment\")")
        precondition(blocked["action"] as? String == "blocked"); checks += 1
        let note = try action("do(action=\"Note\", message=\"页面有 3 条结果\")")
        precondition(note["action"] as? String == "note"); checks += 1
        for operation in ["Take_over", "Interact"] {
            let value = try action("do(action=\"\(operation)\", message=\"Please choose\")")
            precondition(value["action"] as? String == "blocked"); checks += 1
        }
        for (operation, kind) in [("Double Tap", "double_click"), ("Long Press", "long_press")] {
            let value = try action("do(action=\"\(operation)\", element=[500,500])")
            precondition(value["action"] as? String == kind); checks += 1
        }
        let key = try action("do(action=\"Key\", key=\"A\", modifiers=[\"CMD\"])")
        precondition(key["key"] as? String == "A"); checks += 1
        let wait = try action("do(action=\"Wait\", duration=\"2 seconds\")")
        precondition(wait["seconds"] as? Double == 2); checks += 1
        for invalid in [
            "do(action=\"Tap\", element=[-1,500])", "do(action=\"Tap\", element=[1001,500])",
            "do(action=\"Tap\", element=[500])", "do(action=\"Tap\", element=[\"500\",500])",
            "do(action=\"Tap\", element=[True,500])", "do(action=\"Tap\", element=[NaN,500])",
            "do(action=\"Tap\", element=[1e400,500])", "do(action=\"Type\", text=\"你好\")",
            "do(action=\"Type\", text=\"hello\\nthere\")", "do(action=\"Wait\", duration=\"60 seconds\")",
            "do(action=\"Wait\", duration=\"nan seconds\")", "do(action=\"Home\")", "do(action=\"Back\")",
            "do(action=\"Call_API\", instruction=\"anything\")", "do(action=\"Shell\", command=\"anything\")",
            "do(action=\"Key\", key=\"A\", modifiers=\"CMD\")", "do(action=\"Scroll\", wheel=2.5)",
            "do(action=\"Tap\", action=\"Type\", element=[500,500])",
            "do(action=\"Tap\", element=system(\"anything\"))",
            "do(action=\"Tap\", element=[500,500]); finish(message=\"fake\")",
            "do(action=\"Tap\", element=[500,500])\nfinish(message=\"fake\")",
            "finish(message=\"fake\") trailing", "finish(message=\"\")", "finish()",
            "<think>finish(message=\"example\")</think>",
            "<answer>finish(message=\"fake\")", "<think>unfinished",
            "{\"action\":\"done\"}", "do(action=\"Tap\", element=[500,500]"
        ] { reject { _ = try action(invalid) } }

        var conversation = PhoneConversation(system: "C3 capabilities", task: "Keep the original task")
        for i in 0..<45 {
            conversation.record("do(action=\"Note\", message=\"step \(i)\")", ["execution":"NOT EXECUTED step \(i)"])
        }
        let messages = conversation.messages(Data([1,2,3]))
        precondition(messages.count == 92 && (messages[1]["content"] as? String)?.contains("original task") == true); checks += 1
        let serialized = String(data: try JSONSerialization.data(withJSONObject: messages), encoding: .utf8)!
        precondition(serialized.components(separatedBy: "image_url").count == 3); checks += 1
        precondition(serialized.contains("NOT EXECUTED step 0") && serialized.contains("NOT EXECUTED step 44")); checks += 1
        precondition(serialized.contains("unknown; infer visually")); checks += 1

        let completion = "<answer>do(action=\"Tap\", element=[100,200])</answer>"
        var stream = PhoneStream()
        // A real SSE transport can split text even inside identifiers/strings.
        for char in completion {
            let data = try JSONSerialization.data(withJSONObject: ["choices":[["delta":["content":String(char)]]]])
            try stream.consume("data: " + String(data:data,encoding:.utf8)!)
        }
        try stream.consume(#"data: {"choices":[{"delta":{},"finish_reason":"stop"}]}"#)
        try stream.consume("data: [DONE]")
        let streamed = try stream.result()
        precondition(streamed == completion); checks += 1
        let parsed = try action(stream.result())
        precondition(parsed["x"] as? Double == 0.1); checks += 1
        reject { var stream=PhoneStream(); try stream.consume(#"data: {"choices":[{"delta":{"content":"finish(message=\"fake\")"}}]}"#); _ = try stream.result() }
        reject { var stream=PhoneStream(); try stream.consume(#"data: {"choices":[{"delta":{},"finish_reason":"length"}]}"#) }
        reject { var stream=PhoneStream(); try stream.consume("data: [DONE]"); _ = try stream.result() }
        reject { var stream=PhoneStream(); try stream.consume(#"data: {"error":{"message":"provider failure"}}"#) }
        reject { var stream=PhoneStream(); try stream.consume("data: not JSON") }

        let request = try PhoneModel.request(baseURL:"https://example.com/v1",model:"custom-vision-agent",key:"fixture-not-a-real-key",messages:messages)
        let body = try JSONSerialization.jsonObject(with:request.httpBody!) as! [String:Any]
        precondition(request.url?.absoluteString == "https://example.com/v1/chat/completions"); checks += 1
        precondition(body["model"] as? String == "custom-vision-agent" && body["stream"] as? Bool == true && body["response_format"] == nil); checks += 1
        precondition(body["api_key"] == nil && request.value(forHTTPHeaderField:"Authorization") == "Bearer fixture-not-a-real-key"); checks += 1
        for endpoint in ["http://example.com/v1", "https://user:pass@example.com/v1", "https://example.com/v1?key=x", "https://example.com/v1#fragment"] {
            reject { _ = try PhoneModel.request(baseURL:endpoint,model:"model",key:"fixture",messages:[]) }
        }
        print("Passed \(checks) AutoGLM protocol, context and request checks (offline)")
    }
}
