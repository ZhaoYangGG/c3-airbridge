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
        print("Passed \(checks) action validation checks")
    }
}
