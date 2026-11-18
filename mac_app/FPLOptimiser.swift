// FPL Optimiser — native macOS shell for the optimiser.
//
// Starts the Python engine (optimizer_web.py) in the background on a free
// loopback port, then shows its interface in a native window. The engine is
// stopped when the app quits, and exits by itself if the app ever crashes.

import Cocoa
import WebKit

// MARK: - Configuration

private let bundleInfo = Bundle.main.infoDictionary ?? [:]
private let projectDir = URL(fileURLWithPath:
    (bundleInfo["FPLProjectDir"] as? String) ?? "/Users/edbeesley/world-cup-fantasy/fpl_project")
private let python = "/usr/bin/python3"
private let serverScript = "optimizer_web.py"
private let appName = "FPL Optimiser"
private let logURL = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Logs/\(appName).log")

/// Page background of the optimiser UI, so the title bar and loading screen blend in.
private let pageBackground = NSColor(srgbRed: 0x16 / 255.0, green: 0x20 / 255.0, blue: 0x2a / 255.0, alpha: 1)
private let windowBackground = NSColor(srgbRed: 0x0f / 255.0, green: 0x14 / 255.0, blue: 0x19 / 255.0, alpha: 1)

// MARK: - Web view without browser-only context-menu items

final class AppWebView: WKWebView {
    private static let browserOnly: Set<String> = [
        "WKMenuItemIdentifierReload", "WKMenuItemIdentifierGoBack", "WKMenuItemIdentifierGoForward",
        "WKMenuItemIdentifierOpenLinkInNewWindow", "WKMenuItemIdentifierDownloadLinkedFile",
        "WKMenuItemIdentifierOpenImageInNewWindow", "WKMenuItemIdentifierDownloadImage",
        "WKMenuItemIdentifierOpenFrameInNewWindow", "WKMenuItemIdentifierInspectElement",
    ]

    override func willOpenMenu(_ menu: NSMenu, with event: NSEvent) {
        for item in menu.items where Self.browserOnly.contains(item.identifier?.rawValue ?? "") {
            menu.removeItem(item)
        }
        // Tidy any separators left dangling at the edges.
        while let first = menu.items.first, first.isSeparatorItem { menu.removeItem(first) }
        while let last = menu.items.last, last.isSeparatorItem { menu.removeItem(last) }
        super.willOpenMenu(menu, with: event)
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow!
    private var webView: AppWebView!
    private var loadingView: NSView!
    private var server: Process?
    private var port: Int = 0
    private var startAttempts = 0
    private var quitConfirmed = false

    private var baseURL: URL { URL(string: "http://127.0.0.1:\(port)")! }

    // MARK: Launch

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.appearance = NSAppearance(named: .darkAqua)
        buildMenus()
        buildWindow()
        startServer()
    }

    private func buildWindow() {
        let frame = NSRect(x: 0, y: 0, width: 1320, height: 900)
        window = NSWindow(contentRect: frame,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = appName
        window.titleVisibility = .hidden          // the page header already shows the name
        window.titlebarAppearsTransparent = true
        window.backgroundColor = pageBackground   // title bar blends into the page header
        window.minSize = NSSize(width: 960, height: 620)
        window.delegate = self
        window.isReleasedWhenClosed = false
        if !window.setFrameUsingName("MainWindow") { window.center() }
        window.setFrameAutosaveName("MainWindow")

        let config = WKWebViewConfiguration()
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        webView = AppWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        webView.allowsMagnification = false
        webView.underPageBackgroundColor = windowBackground
        webView.setValue(false, forKey: "drawsBackground")   // no white flash before the page paints
        webView.isHidden = true

        loadingView = makeLoadingView()

        let content = NSView()
        content.wantsLayer = true
        content.layer?.backgroundColor = windowBackground.cgColor
        for v in [webView!, loadingView!] {
            v.translatesAutoresizingMaskIntoConstraints = false
            content.addSubview(v)
            NSLayoutConstraint.activate([
                v.leadingAnchor.constraint(equalTo: content.leadingAnchor),
                v.trailingAnchor.constraint(equalTo: content.trailingAnchor),
                v.topAnchor.constraint(equalTo: content.topAnchor),
                v.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            ])
        }
        window.contentView = content
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func makeLoadingView() -> NSView {
        let v = NSView()
        let spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.controlSize = .regular
        spinner.startAnimation(nil)
        let label = NSTextField(labelWithString: "Starting FPL Optimiser…")
        label.textColor = .secondaryLabelColor
        label.font = .systemFont(ofSize: 13)
        let stack = NSStackView(views: [spinner, label])
        stack.orientation = .vertical
        stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        v.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: v.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: v.centerYAnchor, constant: -20),
        ])
        return v
    }

    // MARK: Engine

    private func startServer() {
        guard FileManager.default.fileExists(atPath: projectDir.appendingPathComponent(serverScript).path) else {
            fatal("Can’t find the optimiser in \(projectDir.path).",
                  detail: "The app runs the engine from that folder. If the project has moved, the app needs rebuilding (mac_app/build.sh).")
            return
        }
        startAttempts += 1
        port = Self.freeLoopbackPort()

        var env = ProcessInfo.processInfo.environment
        for (k, v) in Self.readDotEnv(projectDir.appendingPathComponent(".env")) { env[k] = v }
        env["OPT_PORT"] = String(port)
        env["OPT_NO_BROWSER"] = "1"
        env["OPT_PARENT_PID"] = String(getpid())
        env["PYTHONUNBUFFERED"] = "1"
        // Apps launched from Finder get a bare PATH; the engine shells out to `gh` for Sync.
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

        try? FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let log = try? FileHandle(forWritingTo: logURL)

        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [serverScript]
        p.currentDirectoryURL = projectDir
        p.environment = env
        p.standardOutput = log
        p.standardError = log
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async { self?.serverExited(proc) }
        }
        do {
            try p.run()
        } catch {
            fatal("Couldn’t start the optimiser engine.", detail: error.localizedDescription)
            return
        }
        server = p
        waitForServer(deadline: Date().addingTimeInterval(25))
    }

    private func waitForServer(deadline: Date) {
        var req = URLRequest(url: baseURL.appendingPathComponent("health"))
        req.timeoutInterval = 1
        URLSession.shared.dataTask(with: req) { [weak self] data, _, _ in
            DispatchQueue.main.async {
                guard let self else { return }
                if let data, String(decoding: data, as: UTF8.self).contains("fpl-optimiser") {
                    self.webView.load(URLRequest(url: self.baseURL))
                } else if self.server?.isRunning == true, Date() < deadline {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { self.waitForServer(deadline: deadline) }
                } else if self.server?.isRunning == true {
                    self.fatal("The optimiser engine didn’t start in time.", detail: Self.logTail())
                }
                // If it already exited, serverExited() handles it.
            }
        }.resume()
    }

    private func serverExited(_ proc: Process) {
        guard !quitConfirmed, proc === server else { return }
        server = nil
        if webView.isHidden && startAttempts < 3 {
            startServer()          // most likely lost the port race; try another
            return
        }
        fatal("The optimiser engine stopped unexpectedly.", detail: Self.logTail())
    }

    private func stopServer() {
        guard let p = server, p.isRunning else { return }
        p.terminate()
        let until = Date().addingTimeInterval(2)
        while p.isRunning && Date() < until { usleep(50_000) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
    }

    // MARK: Quit / close

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        NSApp.terminate(nil)       // single-window app: closing the window quits (with the busy check)
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitConfirmed || server?.isRunning != true || webView.isHidden { return .terminateNow }
        var req = URLRequest(url: baseURL.appendingPathComponent("busy"))
        req.timeoutInterval = 2
        URLSession.shared.dataTask(with: req) { data, _, _ in
            let running = (data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any])?["running"] as? Int ?? 0
            DispatchQueue.main.async {
                guard running > 0 else { NSApp.reply(toApplicationShouldTerminate: true); return }
                let alert = NSAlert()
                alert.messageText = running == 1 ? "A search is still running" : "\(running) searches are still running"
                alert.informativeText = "Quitting now will stop \(running == 1 ? "it" : "them"), and the results will be lost."
                alert.addButton(withTitle: "Quit")
                alert.addButton(withTitle: "Cancel")
                alert.alertStyle = .warning
                // Losing a long search shouldn't be one reflexive Return away.
                alert.buttons[0].hasDestructiveAction = true
                alert.buttons[0].keyEquivalent = ""
                alert.buttons[1].keyEquivalent = "\r"
                alert.beginSheetModal(for: self.window) { resp in
                    NSApp.reply(toApplicationShouldTerminate: resp == .alertFirstButtonReturn)
                }
            }
        }.resume()
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        quitConfirmed = true
        stopServer()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }

    // MARK: Web view delegates

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard webView.isHidden else { return }
        webView.alphaValue = 0
        webView.isHidden = false
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.18
            webView.animator().alphaValue = 1
            loadingView.animator().alphaValue = 0
        }, completionHandler: { self.loadingView.isHidden = true })
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        // Anything that isn't the local engine opens in the user's browser instead.
        if let url = action.request.url, url.host != "127.0.0.1", url.scheme != "about" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        webView.reload()
    }

    // MARK: Menu actions

    @objc private func reloadPage(_ sender: Any?) {
        if !webView.isHidden { webView.reload() }
    }
    @objc private func zoomIn(_ sender: Any?) { webView.pageZoom = min(webView.pageZoom + 0.1, 2.0) }
    @objc private func zoomOut(_ sender: Any?) { webView.pageZoom = max(webView.pageZoom - 0.1, 0.6) }
    @objc private func actualSize(_ sender: Any?) { webView.pageZoom = 1.0 }
    @objc private func showLog(_ sender: Any?) { NSWorkspace.shared.open(logURL) }

    // MARK: Menus

    private func buildMenus() {
        let main = NSMenu()

        let app = NSMenu()
        app.addItem(withTitle: "About \(appName)", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: "Hide \(appName)", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let hideOthers = app.addItem(withTitle: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        app.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: "Quit \(appName)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        addSubmenu(app, titled: appName, to: main)

        let file = NSMenu(title: "File")
        file.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        addSubmenu(file, titled: "File", to: main)

        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        addSubmenu(edit, titled: "Edit", to: main)

        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload", action: #selector(reloadPage(_:)), keyEquivalent: "r").target = self
        view.addItem(.separator())
        view.addItem(withTitle: "Actual Size", action: #selector(actualSize(_:)), keyEquivalent: "0").target = self
        view.addItem(withTitle: "Zoom In", action: #selector(zoomIn(_:)), keyEquivalent: "+").target = self
        view.addItem(withTitle: "Zoom Out", action: #selector(zoomOut(_:)), keyEquivalent: "-").target = self
        view.addItem(.separator())
        let full = view.addItem(withTitle: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        full.keyEquivalentModifierMask = [.command, .control]
        addSubmenu(view, titled: "View", to: main)

        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Minimise", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        win.addItem(.separator())
        win.addItem(withTitle: "Bring All to Front", action: #selector(NSApplication.arrangeInFront(_:)), keyEquivalent: "")
        addSubmenu(win, titled: "Window", to: main)
        NSApp.windowsMenu = win

        let help = NSMenu(title: "Help")
        help.addItem(withTitle: "Show Engine Log", action: #selector(showLog(_:)), keyEquivalent: "").target = self
        addSubmenu(help, titled: "Help", to: main)
        NSApp.helpMenu = help

        NSApp.mainMenu = main
    }

    private func addSubmenu(_ menu: NSMenu, titled title: String, to main: NSMenu) {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.submenu = menu
        main.addItem(item)
    }

    // MARK: Helpers

    private func fatal(_ message: String, detail: String) {
        let alert = NSAlert()
        alert.messageText = message
        alert.informativeText = detail
        alert.alertStyle = .critical
        alert.addButton(withTitle: "Quit")
        alert.addButton(withTitle: "Show Log")
        if alert.runModal() == .alertSecondButtonReturn { NSWorkspace.shared.open(logURL) }
        quitConfirmed = true
        NSApp.terminate(nil)
    }

    private static func logTail(lines: Int = 8) -> String {
        guard let text = try? String(contentsOf: logURL, encoding: .utf8) else { return "No log output." }
        let tail = text.split(separator: "\n", omittingEmptySubsequences: true)
            .filter { !$0.contains("NotOpenSSLWarning") && !$0.contains("warnings.warn") }
            .suffix(lines)
        return tail.isEmpty ? "No log output." : tail.joined(separator: "\n")
    }

    /// Ask the kernel for an unused loopback port.
    private static func freeLoopbackPort() -> Int {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return 18765 }
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        addr.sin_port = 0
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        let ok = withUnsafeMutablePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(fd, $0, len) == 0 && Darwin.getsockname(fd, $0, &len) == 0
            }
        }
        return ok ? Int(UInt16(bigEndian: addr.sin_port)) : 18765
    }

    /// KEY=VALUE lines from the project's .env (Supabase credentials).
    private static func readDotEnv(_ url: URL) -> [String: String] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [:] }
        var out: [String: String] = [:]
        for raw in text.split(whereSeparator: \.isNewline) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty, !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { continue }
            let key = line[..<eq].trimmingCharacters(in: .whitespaces)
            var value = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces)
            if value.count >= 2, let f = value.first, f == value.last, f == "\"" || f == "'" {
                value = String(value.dropFirst().dropLast())
            }
            out[key] = value
        }
        return out
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
