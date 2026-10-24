import Foundation

@main struct ActionsTests {
    @MainActor static func main() throws {
        var checks=0
        func expectRejected(_ action: [String:Any]) {
            do { try Actions.validate(action);fatalError("Unexpected accepted action: \(action)") }
            catch { checks+=1 }
        }
        try Actions.validate(["action":"click","x":0.2,"y":0.8]);checks+=1
        for value: Any in [-0.1,1.1,Double.infinity,Double.nan,true,"0.5"] { expectRejected(["action":"click","x":value,"y":0.5]) }
        expectRejected(["action":"swipe","x":0.1,"y":0.2,"end_x":0.5])
        expectRejected(["action":"scroll","wheel":1.5])
        expectRejected(["action":"scroll","wheel":11])
        expectRejected(["action":"wait","seconds":-1])
        expectRejected(["action":"wait","seconds":60])
        expectRejected(["action":"text","text":"hi\nthere"])
        expectRejected(["action":"text","text":"你好"])
        expectRejected(["action":"text","text":String(repeating:"a",count:161)])
        expectRejected(["action":"key","key":"DELETE_ALL"])
        expectRejected(["action":"key","key":"A","modifiers":["SYSTEM"]])
        expectRejected(["action":"key","key":"A","modifiers":"CMD"])
        expectRejected(["action":"shell","command":"anything"])
        let plus=try Actions.text("27+58")
        precondition(plus.count==5 && plus[2]["usage"] as? Int==46 && plus[2]["modifiers"] as? Int==2);checks+=1
        let shortcut=try Actions.key("SPACE",["CMD"])
        precondition(shortcut["usage"] as? Int==44 && shortcut["modifiers"] as? Int==8);checks+=1
        try Actions.validate(["action":"done","reason":"visible result"]);checks+=1
        let search=try Actions.key("SEARCH")
        precondition(search["op"] as? String=="consumer" && search["usage"] as? Int==545);checks+=1
        expectRejected(["action":"key","key":"SEARCH","modifiers":["CMD"]])
        let ready: [String:Any]=["allowed":true,"input_ready":true,"app_kind":"calculator","current_value":"0","expression_empty":true,"evidence":"Display 0, no pending expression","input_rect":[0.05,0.2,0.9,0.2]]
        precondition(EntryCheck.allows(ready));checks+=1
        for value in ["0.27","0.","27+58",""] {
            var reply=ready;reply["current_value"]=value
            precondition(!EntryCheck.allows(reply));checks+=1
        }
        for key in ["expression_empty","input_ready","allowed"] {
            var reply=ready;reply[key]=false
            precondition(!EntryCheck.allows(reply));checks+=1
        }
        var malformed=ready;malformed["allowed"]=1
        precondition(!EntryCheck.allows(malformed));checks+=1
        malformed=ready;malformed["input_rect"]=[0.5,0.2,0.2,0.1]
        precondition(!EntryCheck.allows(malformed));checks+=1
        malformed=ready;malformed["input_rect"]=[0.05,0.9,0.9,0.2]
        precondition(!EntryCheck.allows(malformed));checks+=1
        malformed=ready;malformed["app_kind"]="other"
        precondition(!EntryCheck.allows(malformed));checks+=1
        let searchReady: [String:Any]=["allowed":true,"input_ready":true,"app_kind":"search","evidence":"Empty search field with caret"]
        precondition(EntryCheck.allows(searchReady));checks+=1
        for raw in ["{\"visible\":false}","```json\n{\"visible\":false}\n```"] {
            let reply=try VisionReply.parse(raw,finishReason:"stop")
            precondition(reply["visible"] as? Bool == false);checks+=1
        }
        for (raw,reason) in [("[]","stop"),("null","stop"),("{\"visible\":true}","length"),("{\"visible\":","stop"),("```json\n{}","stop")] {
            do {_ = try VisionReply.parse(raw,finishReason:reason);fatalError("Accepted invalid vision reply")}
            catch is VisionReplyError {checks+=1}
        }
        print("Passed \(checks) action validation checks")
    }
}
