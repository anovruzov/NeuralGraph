// NeuralGraph Desktop: a native macOS shell around the neuralgraph-memory dashboard.
//
// The app runs the bundled `neuralgraph-memory serve` on a free loopback port and shows its dashboard in a
// WKWebView. Export Brain / Import Brain / Connect Agents are native: Save and Open panels, privacy warnings and
// confirmations, and the round-trip verification all go through the `neuralgraph-memory brain` CLI. Archives are
// written only where the user saves them; nothing is uploaded.

import AppKit
import UniformTypeIdentifiers
import WebKit

// MARK: - CLI bridge

struct CLIResult {
    let status: Int32
    let stdout: String
    let stderr: String
    var json: [String: Any]? {
        guard let start = stdout.firstIndex(of: "{"), let data = String(stdout[start...]).data(using: .utf8) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }
    var message: String {
        let e = stderr.trimmingCharacters(in: .whitespacesAndNewlines)
        return e.isEmpty ? stdout.trimmingCharacters(in: .whitespacesAndNewlines) : e
    }
}

enum CLI {
    static var binary: String {
        if let p = Bundle.main.path(forResource: "neuralgraph-memory", ofType: nil) { return p }
        return ProcessInfo.processInfo.environment["NEURALGRAPH_MEMORY_BIN"] ?? "/usr/local/bin/neuralgraph-memory"
    }

    /// Run the CLI off the main thread and deliver the result on it.
    static func run(_ args: [String], completion: @escaping (CLIResult) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let r = runSync(args)
            DispatchQueue.main.async { completion(r) }
        }
    }

    static func runSync(_ args: [String]) -> CLIResult {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: binary)
        p.arguments = args
        var env = ProcessInfo.processInfo.environment
        env["RUST_LOG"] = "warn"
        p.environment = env
        let out = Pipe(), err = Pipe()
        p.standardOutput = out
        p.standardError = err
        do { try p.run() } catch {
            return CLIResult(status: -1, stdout: "", stderr: "cannot run \(binary): \(error.localizedDescription)")
        }
        let o = out.fileHandleForReading.readDataToEndOfFile()
        let e = err.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return CLIResult(status: p.terminationStatus, stdout: String(decoding: o, as: UTF8.self), stderr: String(decoding: e, as: UTF8.self))
    }
}

func freePort() -> UInt16 {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    defer { close(fd) }
    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    addr.sin_port = 0
    var len = socklen_t(MemoryLayout<sockaddr_in>.size)
    _ = withUnsafeMutablePointer(to: &addr) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, len) } }
    _ = withUnsafeMutablePointer(to: &addr) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &len) } }
    let port = UInt16(bigEndian: addr.sin_port)
    return port == 0 ? 8765 : port
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var status: NSTextField!
    var server: Process?
    var port: UInt16 = 0
    var agentsWindow: NSWindow?

    var dbPath: String {
        get { UserDefaults.standard.string(forKey: "dbPath") ?? (NSHomeDirectory() + "/.neuralgraph/chat_memory.db") }
        set { UserDefaults.standard.set(newValue, forKey: "dbPath") }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        startServer()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) { stopServer() }

    // MARK: UI

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About NeuralGraph", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit NeuralGraph", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let brainItem = NSMenuItem()
        main.addItem(brainItem)
        let brain = NSMenu(title: "Brain")
        brain.addItem(withTitle: "Export Brain…", action: #selector(exportBrain), keyEquivalent: "e")
        brain.addItem(withTitle: "Import Brain…", action: #selector(importBrain), keyEquivalent: "i")
        brain.addItem(.separator())
        brain.addItem(withTitle: "Connect Agents…", action: #selector(showAgents), keyEquivalent: "k")
        brain.addItem(withTitle: "Open Brain Database…", action: #selector(chooseDatabase), keyEquivalent: "o")
        brain.addItem(withTitle: "Show Database in Finder", action: #selector(revealDatabase), keyEquivalent: "")
        brain.addItem(.separator())
        brain.addItem(withTitle: "Reload Dashboard", action: #selector(reload), keyEquivalent: "r")
        brainItem.submenu = brain

        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        NSApp.mainMenu = main
    }

    func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1240, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "NeuralGraph"
        window.center()
        window.setFrameAutosaveName("NeuralGraphMain")

        let root = NSView()
        let bar = NSStackView()
        bar.orientation = .horizontal
        bar.spacing = 8
        bar.edgeInsets = NSEdgeInsets(top: 8, left: 12, bottom: 8, right: 12)
        let exportBtn = NSButton(title: "Export Brain…", target: self, action: #selector(exportBrain))
        exportBtn.bezelStyle = .rounded
        exportBtn.keyEquivalent = "\r"
        exportBtn.setAccessibilityIdentifier("exportBrain")
        let importBtn = NSButton(title: "Import Brain…", target: self, action: #selector(importBrain))
        importBtn.bezelStyle = .rounded
        importBtn.setAccessibilityIdentifier("importBrain")
        let agentsBtn = NSButton(title: "Connect Agents…", target: self, action: #selector(showAgents))
        agentsBtn.bezelStyle = .rounded
        status = NSTextField(labelWithString: "Starting…")
        status.textColor = .secondaryLabelColor
        status.lineBreakMode = .byTruncatingMiddle
        status.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        bar.addArrangedSubview(exportBtn)
        bar.addArrangedSubview(importBtn)
        bar.addArrangedSubview(agentsBtn)
        bar.addArrangedSubview(status)

        web = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        web.navigationDelegate = self
        for v in [bar, web!] as [NSView] {
            v.translatesAutoresizingMaskIntoConstraints = false
            root.addSubview(v)
        }
        NSLayoutConstraint.activate([
            bar.topAnchor.constraint(equalTo: root.topAnchor),
            bar.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            bar.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            web.topAnchor.constraint(equalTo: bar.bottomAnchor),
            web.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            web.bottomAnchor.constraint(equalTo: root.bottomAnchor),
        ])
        window.contentView = root
        window.makeKeyAndOrderFront(nil)
    }

    func setStatus(_ s: String) { status.stringValue = s }

    // MARK: Server

    func startServer() {
        try? FileManager.default.createDirectory(atPath: (dbPath as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
        port = freePort()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: CLI.binary)
        p.arguments = ["serve", "--db", dbPath, "--host", "127.0.0.1", "--port", String(port)]
        let log = FileHandle(forWritingAtPath: logPath()) ?? FileHandle.nullDevice
        log.seekToEndOfFile()
        p.standardOutput = log
        p.standardError = log
        do { try p.run() } catch {
            setStatus("Could not start the memory server: \(error.localizedDescription)")
            return
        }
        server = p
        setStatus("Brain: \(dbPath)")
        waitForServer(attempt: 0)
    }

    func logPath() -> String {
        let dir = NSHomeDirectory() + "/Library/Logs/NeuralGraph"
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        let path = dir + "/server.log"
        if !FileManager.default.fileExists(atPath: path) { FileManager.default.createFile(atPath: path, contents: nil) }
        return path
    }

    func waitForServer(attempt: Int) {
        let url = URL(string: "http://127.0.0.1:\(port)/healthz")!
        URLSession.shared.dataTask(with: url) { _, resp, _ in
            DispatchQueue.main.async {
                if (resp as? HTTPURLResponse)?.statusCode == 200 {
                    self.web.load(URLRequest(url: URL(string: "http://127.0.0.1:\(self.port)/")!))
                } else if attempt < 100, self.server?.isRunning == true {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { self.waitForServer(attempt: attempt + 1) }
                } else {
                    self.setStatus("The memory server did not start; see ~/Library/Logs/NeuralGraph/server.log")
                }
            }
        }.resume()
    }

    func stopServer() {
        guard let p = server, p.isRunning else { server = nil; return }
        p.interrupt()
        let deadline = Date().addingTimeInterval(5)
        while p.isRunning && Date() < deadline { usleep(50_000) }
        if p.isRunning { p.terminate() }
        p.waitUntilExit()
        server = nil
    }

    @objc func reload() { web.reload() }

    // MARK: Export

    @objc func exportBrain() {
        setStatus("Checking what the export will contain…")
        CLI.run(["brain", "preview", "--db", dbPath]) { r in
            guard r.status == 0, let pv = r.json, let stores = pv["stores"] as? [[String: Any]], let store = stores.first else {
                self.fail("Export failed", r.message)
                return
            }
            self.exportOptions(store: store, confidential: (pv["confidential"] as? Bool) ?? false)
        }
    }

    func exportOptions(store: [String: Any], confidential: Bool) {
        let counts = store["counts"] as? [String: Any] ?? [:]
        let chats = store["chats"] as? [[String: Any]] ?? []
        let alert = NSAlert()
        alert.messageText = "Export Brain"
        alert.informativeText = """
            \(counts["memories"] ?? 0) memories, \(counts["messages"] ?? 0) raw messages, \(counts["entities"] ?? 0) entities, \
            \(counts["relations"] ?? 0) relations, \(counts["memory_links"] ?? 0) links, \(counts["audit_log"] ?? 0) audit entries.

            The archive holds SQLite, JSONL, Markdown and GraphML copies with a checksummed manifest. \
            It is saved only where you choose and is never uploaded.
            """
        let scope = NSPopUpButton(frame: NSRect(x: 0, y: 28, width: 360, height: 26))
        scope.addItem(withTitle: "Whole brain")
        for c in chats {
            let id = c["chat_id"] as? String ?? ""
            let title = (c["title"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? id
            scope.addItem(withTitle: "Project: \(title)")
            scope.lastItem?.representedObject = id
        }
        let emb = NSButton(checkboxWithTitle: "Include embeddings (vectors + model and dimension)", target: nil, action: nil)
        emb.frame = NSRect(x: 0, y: 0, width: 360, height: 22)
        emb.state = .on
        let box = NSView(frame: NSRect(x: 0, y: 0, width: 360, height: 56))
        box.addSubview(scope)
        box.addSubview(emb)
        alert.accessoryView = box
        alert.addButton(withTitle: "Choose Location…")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { resp in
            guard resp == .alertFirstButtonReturn else { self.setStatus("Export cancelled"); return }
            let chat = scope.selectedItem?.representedObject as? String
            self.confirmPrivacy(confidential: confidential, store: store) {
                self.saveArchive(chat: chat, embeddings: emb.state == .on, allowConfidential: confidential)
            }
        }
    }

    func confirmPrivacy(confidential: Bool, store: [String: Any], then: @escaping () -> Void) {
        guard confidential else { then(); return }
        let findings = (store["privacy"] as? [[String: Any]] ?? []).map { "• \($0["count"] ?? 0) item(s) that look like \($0["category"] ?? "")" }
        let a = NSAlert()
        a.alertStyle = .warning
        a.messageText = "This brain may contain confidential content"
        a.informativeText = findings.joined(separator: "\n") + "\n\nAnyone with the archive can read it. Export anyway?"
        a.addButton(withTitle: "Export Anyway")
        a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in
            if r == .alertFirstButtonReturn { then() } else { self.setStatus("Export cancelled") }
        }
    }

    func saveArchive(chat: String?, embeddings: Bool, allowConfidential: Bool) {
        let panel = NSSavePanel()
        panel.title = "Export Brain"
        panel.prompt = "Export"
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmmss"
        panel.nameFieldStringValue = "neuralgraph-brain-\(chat.map { $0 + "-" } ?? "")\(f.string(from: Date())).zip"
        panel.allowedContentTypes = [.zip]
        panel.canCreateDirectories = true
        panel.beginSheetModal(for: window) { resp in
            guard resp == .OK, let url = panel.url else { self.setStatus("Export cancelled"); return }
            var args = ["brain", "export", "--db", self.dbPath, "--out", url.path, "--overwrite", "--json"]
            if let c = chat { args += ["--chat", c] }
            if !embeddings { args.append("--no-embeddings") }
            if allowConfidential { args.append("--allow-confidential") }
            self.setStatus("Exporting…")
            CLI.run(args) { r in
                guard r.status == 0, let j = r.json else { self.fail("Export failed", r.message); return }
                let bytes = (j["bytes"] as? Int) ?? 0
                self.setStatus("Exported \(url.lastPathComponent)")
                let a = NSAlert()
                a.messageText = "Brain exported"
                a.informativeText = "\(url.path)\n\(ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)), archive \(j["manifest"].flatMap { ($0 as? [String: Any])?["archive_id"] } ?? "")"
                a.addButton(withTitle: "Show in Finder")
                a.addButton(withTitle: "Done")
                a.beginSheetModal(for: self.window) { r in
                    if r == .alertFirstButtonReturn { NSWorkspace.shared.activateFileViewerSelecting([url]) }
                }
            }
        }
    }

    // MARK: Import

    @objc func importBrain() {
        let panel = NSOpenPanel()
        panel.title = "Import Brain"
        panel.prompt = "Import"
        panel.allowedContentTypes = [.zip]
        panel.allowsMultipleSelection = false
        panel.beginSheetModal(for: window) { resp in
            guard resp == .OK, let url = panel.url else { return }
            self.setStatus("Checking \(url.lastPathComponent)…")
            CLI.run(["brain", "inspect", url.path]) { r in
                guard r.status == 0, let m = r.json else { self.fail("This archive cannot be imported", r.message); return }
                self.chooseImportMode(url: url, manifest: m)
            }
        }
    }

    func chooseImportMode(url: URL, manifest m: [String: Any]) {
        let store = (m["stores"] as? [[String: Any]])?.first ?? [:]
        let c = store["counts"] as? [String: Any] ?? [:]
        let e = store["embeddings"] as? [String: Any] ?? [:]
        let scope = (m["scope"] as? [String: Any])?["kind"] as? String ?? "full"
        let privacy = m["privacy"] as? [String: Any] ?? [:]
        let a = NSAlert()
        a.messageText = "Import \(url.lastPathComponent)"
        a.informativeText = """
            Created \(m["created_at"] ?? "?") · \(scope) export · checksums verified
            \(c["memories"] ?? 0) memories, \(c["messages"] ?? 0) messages, \(c["entities"] ?? 0) entities, \(c["relations"] ?? 0) relations
            Embeddings: \(e["count"] ?? 0) × \(e["dim"] ?? "?") from \(e["model"] ?? "?")
            \((privacy["confidential"] as? Bool) == true ? "\n⚠︎ The archive may contain confidential content.\n" : "")
            Merge adds this knowledge to your current brain without overwriting anything (colliding ids get a new id; \
            history is kept). Restore replaces the current brain; its files are kept as a backup.
            """
        a.addButton(withTitle: "Merge")
        a.addButton(withTitle: "Restore…")
        a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in
            switch r {
            case .alertFirstButtonReturn: self.runImport(url: url, merge: true)
            case .alertSecondButtonReturn: self.confirmRestore(url: url)
            default: self.setStatus("Import cancelled")
            }
        }
    }

    func confirmRestore(url: URL) {
        let exists = FileManager.default.fileExists(atPath: dbPath)
        guard exists else { runImport(url: url, merge: false); return }
        let a = NSAlert()
        a.alertStyle = .warning
        a.messageText = "Replace the current brain?"
        a.informativeText = "The current database is moved aside (…before-import-<time>) next to \(dbPath), not deleted."
        a.addButton(withTitle: "Replace")
        a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in
            if r == .alertFirstButtonReturn { self.runImport(url: url, merge: false) } else { self.setStatus("Import cancelled") }
        }
    }

    func runImport(url: URL, merge: Bool) {
        setStatus("Importing…")
        web.loadHTMLString("<body style='font:15px -apple-system;padding:40px;color:#888'>Importing…</body>", baseURL: nil)
        let db = dbPath
        DispatchQueue.global(qos: .userInitiated).async {
            DispatchQueue.main.sync { self.stopServer() }
            var args = ["brain", "import", url.path, "--db", db, "--json"]
            args.append(merge ? "--merge" : "--replace")
            let imp = CLI.runSync(args)
            var ver: CLIResult?
            if imp.status == 0 {
                var vargs = ["brain", "verify", url.path, "--db", db, "--json"]
                if merge { vargs.append("--merge") }
                ver = CLI.runSync(vargs)
            }
            DispatchQueue.main.async {
                self.startServer()
                guard imp.status == 0 else { self.fail("Import failed", imp.message); return }
                let v = ver?.json
                let ok = (v?["ok"] as? Bool) == true
                let lines = (v?["checks"] as? [[String: Any]] ?? []).map { "\(($0["ok"] as? Bool) == true ? "✓" : "✗") \($0["name"] ?? ""): \($0["detail"] ?? "")" }
                self.setStatus(ok ? "Imported and verified" : "Imported; verification reported problems")
                let a = NSAlert()
                a.alertStyle = ok ? .informational : .warning
                a.messageText = ok ? "Brain imported and verified" : "Brain imported, but verification failed"
                a.informativeText = lines.joined(separator: "\n")
                a.addButton(withTitle: "Connect Agents…")
                a.addButton(withTitle: "Done")
                a.beginSheetModal(for: self.window) { r in if r == .alertFirstButtonReturn { self.showAgents() } }
            }
        }
    }

    // MARK: Agents

    @objc func showAgents() {
        let bin = CLI.binary, db = dbPath
        let mcp = """
            {
              "mcpServers": {
                "neuralgraph-memory": { "command": "\(bin)", "args": ["mcp", "--db", "\(db)"] }
              }
            }
            """
        let items: [(String, String)] = [
            ("Claude Code (run in Terminal)", "claude mcp add neuralgraph-memory -- \"\(bin)\" mcp --db \"\(db)\""),
            ("Gemini CLI: merge into ~/.gemini/settings.json", mcp),
            ("Codex: append to ~/.codex/config.toml", "[mcp_servers.neuralgraph-memory]\ncommand = \"\(bin)\"\nargs = [\"mcp\", \"--db\", \"\(db)\"]"),
            ("Any MCP client over HTTP (while this app is open)", "http://127.0.0.1:\(port)/mcp"),
        ]
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 720, height: 560), styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        w.title = "Connect Agents"
        w.isReleasedWhenClosed = false
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 6
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        let intro = NSTextField(wrappingLabelWithString: "Agents read this brain through the NeuralGraph MCP server. Search results include their source messages, so answers stay attributable. Restart an agent after importing a brain.")
        stack.addArrangedSubview(intro)
        for (title, text) in items {
            let label = NSTextField(labelWithString: title)
            label.font = .boldSystemFont(ofSize: 12)
            let field = NSTextField(wrappingLabelWithString: text)
            field.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
            field.isSelectable = true
            let copy = NSButton(title: "Copy", target: self, action: #selector(copySnippet(_:)))
            copy.identifier = NSUserInterfaceItemIdentifier(text)
            let row = NSStackView(views: [label, copy])
            stack.addArrangedSubview(row)
            stack.addArrangedSubview(field)
            field.widthAnchor.constraint(equalToConstant: 680).isActive = true
        }
        w.contentView = stack
        w.center()
        w.makeKeyAndOrderFront(nil)
        agentsWindow = w
    }

    @objc func copySnippet(_ sender: NSButton) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(sender.identifier?.rawValue ?? "", forType: .string)
        setStatus("Copied to the clipboard")
    }

    // MARK: Database

    @objc func chooseDatabase() {
        let panel = NSOpenPanel()
        panel.title = "Open Brain Database"
        panel.allowedContentTypes = []
        panel.allowsOtherFileTypes = true
        panel.directoryURL = URL(fileURLWithPath: (dbPath as NSString).deletingLastPathComponent)
        panel.beginSheetModal(for: window) { r in
            guard r == .OK, let url = panel.url else { return }
            self.stopServer()
            self.dbPath = url.path
            self.startServer()
        }
    }

    @objc func revealDatabase() {
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: dbPath)])
    }

    func fail(_ title: String, _ detail: String) {
        setStatus(title)
        let a = NSAlert()
        a.alertStyle = .warning
        a.messageText = title
        a.informativeText = detail
        a.beginSheetModal(for: window, completionHandler: nil)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
