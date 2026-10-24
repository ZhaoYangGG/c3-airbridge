import UIKit

@main final class AppDelegate: UIResponder, UIApplicationDelegate {
    func application(_ application: UIApplication, configurationForConnecting session: UISceneSession, options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        let config=UISceneConfiguration(name:"Agent",sessionRole:session.role);config.delegateClass=AgentScene.self;return config
    }
}
final class AgentScene: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    var controller: AgentController!
    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options: UIScene.ConnectionOptions) {
        guard let scene=scene as? UIWindowScene else{return}
        controller=AgentController();window=UIWindow(windowScene:scene);window?.rootViewController=controller;window?.makeKeyAndVisible()
        UIApplication.shared.isIdleTimerDisabled=true
    }
    func sceneDidBecomeActive(_ scene: UIScene) { controller?.state="active" }
    func sceneWillResignActive(_ scene: UIScene) { controller?.state="inactive" }
    func sceneDidEnterBackground(_ scene: UIScene) { controller?.state="background" }
}

@MainActor final class AgentController: UIViewController {
    private let settings=Settings()
    private let bluetooth=Bluetooth()
    private let capture=Capture()
    private lazy var model=Model(settings)
    private lazy var agent=Agent(bluetooth,capture,model)
    private let taskInput=UITextView()
    private let keyInput=UITextField()
    private let endpointInput=UITextField()
    private let modelInput=UITextField()
    private let status=UILabel()
    private let progress=UILabel()
    private let logs=UILabel()
    private var timer: Timer?
    private var heartbeat=0
    private var queuedTask: String?
    private var queuedSteps=24
    private var diagnosticBusy=false
    var state="launch" { didSet { if state=="active" {consumeInbox()};persist() } }
    private let session=UUID().uuidString

    override func viewDidLoad() {
        super.viewDidLoad();settings.importBootstrap();view.backgroundColor = .systemBackground
        let scroll=UIScrollView();scroll.translatesAutoresizingMaskIntoConstraints=false;view.addSubview(scroll)
        let stack=UIStackView();stack.axis = .vertical;stack.spacing=16;stack.translatesAutoresizingMaskIntoConstraints=false;scroll.addSubview(stack)
        NSLayoutConstraint.activate([
            scroll.leadingAnchor.constraint(equalTo:view.safeAreaLayoutGuide.leadingAnchor),scroll.trailingAnchor.constraint(equalTo:view.safeAreaLayoutGuide.trailingAnchor),
            scroll.topAnchor.constraint(equalTo:view.safeAreaLayoutGuide.topAnchor),scroll.bottomAnchor.constraint(equalTo:view.bottomAnchor),
            stack.leadingAnchor.constraint(equalTo:scroll.contentLayoutGuide.leadingAnchor,constant:22),stack.trailingAnchor.constraint(equalTo:scroll.contentLayoutGuide.trailingAnchor,constant:-22),
            stack.topAnchor.constraint(equalTo:scroll.contentLayoutGuide.topAnchor,constant:18),stack.bottomAnchor.constraint(equalTo:scroll.contentLayoutGuide.bottomAnchor,constant:-30),
            stack.widthAnchor.constraint(equalTo:scroll.frameLayoutGuide.widthAnchor,constant:-44)])
        let title=UILabel();title.text="C3 Agent";title.font = .boldSystemFont(ofSize:32);stack.addArrangedSubview(title)
        let intro=UILabel();intro.text="写下任务，手机负责观察与思考，ESP32-C3 负责蓝牙键鼠操作。";intro.numberOfLines=0;stack.addArrangedSubview(intro)
        status.numberOfLines=0;status.font = .monospacedSystemFont(ofSize:14,weight:.medium);stack.addArrangedSubview(status)
        taskInput.text="打开计算器，计算 12+34，确认结果是 46。";taskInput.font = .systemFont(ofSize:18);taskInput.backgroundColor = .secondarySystemBackground;taskInput.layer.cornerRadius=14
        taskInput.accessibilityLabel="任务内容";taskInput.heightAnchor.constraint(equalToConstant:110).isActive=true;stack.addArrangedSubview(taskInput)
        let start=button("开始任务",#selector(startTask));start.configuration = .filled();start.configuration?.title="开始任务";stack.addArrangedSubview(start)
        let row=UIStackView(arrangedSubviews:[button("停止任务",#selector(stopTask)),button("连接 C3",#selector(connectBoard)),button("结束共享",#selector(stopSharing))]);row.distribution = .fillEqually;stack.addArrangedSubview(row)
        stack.addArrangedSubview(button("开始屏幕共享",#selector(startSharing)))
        progress.numberOfLines=0;progress.font = .systemFont(ofSize:16,weight:.medium);stack.addArrangedSubview(progress)
        logs.numberOfLines=0;logs.font = .monospacedSystemFont(ofSize:12,weight:.regular);logs.textColor = .secondaryLabel;stack.addArrangedSubview(logs)
        let configTitle=UILabel();configTitle.text="模型配置";configTitle.font = .boldSystemFont(ofSize:20);stack.addArrangedSubview(configTitle)
        endpointInput.text=settings.baseURL;endpointInput.placeholder="HTTPS API Base URL";endpointInput.keyboardType = .URL
        modelInput.text=settings.model;modelInput.placeholder="模型名称"
        keyInput.placeholder=settings.apiKey.isEmpty ? "API Key" : "API Key 已安全保存；填写可替换";keyInput.isSecureTextEntry=true
        for field in [endpointInput,modelInput,keyInput] { field.borderStyle = .roundedRect;field.autocapitalizationType = .none;field.autocorrectionType = .no;stack.addArrangedSubview(field) }
        stack.addArrangedSubview(button("保存模型配置",#selector(saveSettings)))
        let help=UILabel();help.numberOfLines=0;help.font = .systemFont(ofSize:13);help.textColor = .secondaryLabel
        help.text="首次使用：给 C3 供电，完成蓝牙配对；在辅助功能中开启辅助触控与全键盘控制。开始任务后，确认共享整个屏幕，再轻点弹层外的空白区域返回 App，任务会自动开始。保持手机解锁。画面会按任务发送给所配置的模型 API。返回本 App 可停止任务。";stack.addArrangedSubview(help)
        bluetooth.changed={ [weak self] in self?.refresh() };capture.changed={ [weak self] in self?.refresh();self?.startQueuedIfReady() }
        capture.stopped={ [weak self] in self?.queuedTask=nil;self?.agent.stop() };agent.changed={ [weak self] in self?.refresh() }
        timer=Timer.scheduledTimer(withTimeInterval:1,repeats:true) { [weak self] _ in
            Task { @MainActor in guard let self else{return};self.heartbeat+=1;self.settings.importBootstrap();self.consumeInbox();self.startQueuedIfReady();self.persist() }
        }
        refresh()
        if ProcessInfo.processInfo.arguments.contains("--present-picker") { DispatchQueue.main.asyncAfter(deadline:.now()+1) { self.capture.present() } }
    }
    private func button(_ title: String,_ action: Selector) -> UIButton {
        let button=UIButton(type:.system);button.setTitle(title,for:.normal);button.addTarget(self,action:action,for:.touchUpInside);button.heightAnchor.constraint(greaterThanOrEqualToConstant:42).isActive=true;return button
    }
    @objc private func connectBoard() { bluetooth.connect() }
    @objc private func saveSettings() {
        guard !agent.running else{return}
        settings.baseURL=endpointInput.text?.trimmingCharacters(in:.whitespacesAndNewlines) ?? ""
        settings.model=modelInput.text?.trimmingCharacters(in:.whitespacesAndNewlines) ?? ""
        if let key=keyInput.text?.trimmingCharacters(in:.whitespacesAndNewlines),!key.isEmpty{settings.apiKey=key;keyInput.text=""}
        keyInput.placeholder=settings.apiKey.isEmpty ? "API Key" : "API Key 已安全保存";view.endEditing(true);refresh()
    }
    @objc private func startTask() {
        view.endEditing(true)
        guard !agent.running,!diagnosticBusy else{return}
        queuedTask=taskInput.text;queuedSteps=24
        if !capture.running { capture.present() } else { startQueuedIfReady() }
    }
    private func startQueuedIfReady() {
        guard let prompt=queuedTask,bluetooth.ready,bluetooth.secure,capture.running,!agent.running,!diagnosticBusy,
              state == "active", Date().timeIntervalSince(capture.startedAt)>1 else{return}
        queuedTask=nil
        view.endEditing(true)
        do { try agent.start(prompt,maxSteps:queuedSteps) } catch { agent.event(error.localizedDescription) }
    }
    @objc private func stopTask() { queuedTask=nil;agent.stop() }
    @objc private func startSharing() {view.endEditing(true);capture.present()}
    @objc private func stopSharing() { queuedTask=nil;agent.stop();Task { await capture.stop() } }
    private func refresh() {
        taskInput.isEditable = !agent.running
        taskInput.isSelectable = !agent.running
        taskInput.isUserInteractionEnabled = !agent.running
        for field in [endpointInput,modelInput,keyInput] { field.isEnabled = !agent.running }
        status.text="\(bluetooth.status)\n\(capture.status) · 模型\(settings.apiKey.isEmpty ? "未配置" : "已配置")"
        progress.text="\(agent.job["state"] as? String ?? "就绪") · 第 \(agent.job["step"] as? Int ?? 0) 步\n\(agent.job["result"] as? String ?? "")"
        logs.text=agent.events.filter{$0["internal"] as? Bool != true}.suffix(6).compactMap{$0["text"] as? String}.joined(separator:"\n");persist()
    }
    private func persist() {
        PrivateFiles.json("status.json",["version":"1.0.0","session":session,"at":Date().timeIntervalSince1970,"app_state":state,
            "heartbeat":heartbeat,"ble_ready":bluetooth.ready,"ble_secure":bluetooth.secure,"ble_status":bluetooth.status,"hid_acknowledgements":bluetooth.acknowledged,
            "capture_running":capture.running,"capture_waiting_for_selection":capture.waitingForSelection,"capture_status":capture.status,"capture_events":capture.events,"frame_sequence":capture.frame?.sequence ?? 0,
            "frame_at":capture.frame?.at.timeIntervalSince1970 ?? 0,"background_frames":capture.backgroundFrames,
            "model_configured":!settings.apiKey.isEmpty,"model_calls":model.calls,"scheduler":"iPhone app","capture_source":"ScreenCaptureKit",
            "job":agent.job,"events":agent.events,"queued":queuedTask != nil,"diagnostic_busy":diagnosticBusy])
    }
    // App-container inbox is development instrumentation only; release builds
    // accept tasks from the on-device task editor, never an unauthenticated URL.
    private func consumeInbox() {
        #if DEBUG
        let path=PrivateFiles.root.appendingPathComponent("inbox.json")
        guard let data=try? Data(contentsOf:path),let obj=try? JSONSerialization.jsonObject(with:data) as? [String:Any],
              let id=obj["id"] as? String,id != UserDefaults.standard.string(forKey:"lastInbox") else{return}
        try? FileManager.default.removeItem(at:path);UserDefaults.standard.set(id,forKey:"lastInbox")
        if obj["stop"] as? Bool==true { stopTask();return }
        if let prompt=obj["prompt"] as? String,!agent.running,!diagnosticBusy {
            taskInput.text=prompt;queuedTask=prompt;queuedSteps=obj["max_steps"] as? Int ?? 24
            if !capture.running,UIApplication.shared.applicationState == .active{capture.present()}
            startQueuedIfReady()
        } else if let commands=obj["commands"] as? [[String:Any]],!agent.running,!diagnosticBusy,commands.count<=80 {
            diagnosticBusy=true
            Task { @MainActor in
                do { for cmd in commands { try await self.bluetooth.send(cmd);try await Task.sleep(for:.milliseconds(120)) };self.agent.event("手机 App → C3 GATT 诊断指令已执行") }
                catch { self.agent.event(error.localizedDescription) }
                self.diagnosticBusy=false;self.refresh()
            }
        }
        #endif
    }
}
