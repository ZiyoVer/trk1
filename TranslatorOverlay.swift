import AppKit
import Darwin

private let projectPath = "/Users/abc/meeting-translator-mvp"

private final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow!
    private var statusDot: NSTextField!
    private var statusLabel: NSTextField!
    private var sourceLanguageLabel: NSTextField!
    private var sourceSubtitle: NSTextField!
    private var targetLanguageLabel: NSTextField!
    private var targetSubtitle: NSTextField!
    private var directionPopup: NSPopUpButton!
    private var startButton: NSButton!
    private var stopButton: NSButton!
    private var process: Process?
    private var pipe: Pipe?
    private var outputBuffer = ""
    private var sourceCaption = ""
    private var targetCaption = ""
    private var lastSourceAt = Date.distantPast
    private var lastTargetAt = Date.distantPast
    private var closing = false
    private var captionTimer: Timer?
    private let directions: [(title: String, source: String, target: String, targetName: String)] = [
        ("English  →  O‘zbekcha", "EN", "uz", "O‘ZBEKCHA"),
        ("Russian  →  O‘zbekcha", "RU", "uz", "O‘ZBEKCHA"),
        ("O‘zbekcha  →  English", "UZ", "en", "ENGLISH"),
        ("O‘zbekcha  →  Russian", "UZ", "ru", "RUSSIAN"),
        ("English  →  Russian", "EN", "ru", "RUSSIAN"),
        ("Russian  →  English", "RU", "en", "ENGLISH"),
    ]

    private let bg = NSColor(calibratedRed: 0.063, green: 0.094, blue: 0.153, alpha: 0.94)
    private let fg = NSColor(calibratedWhite: 0.98, alpha: 1)
    private let muted = NSColor(calibratedRed: 0.58, green: 0.65, blue: 0.75, alpha: 1)
    private let green = NSColor(calibratedRed: 0.13, green: 0.77, blue: 0.37, alpha: 1)
    private let red = NSColor(calibratedRed: 0.94, green: 0.27, blue: 0.27, alpha: 1)
    private let amber = NSColor(calibratedRed: 0.96, green: 0.62, blue: 0.04, alpha: 1)
    private let disabled = NSColor(calibratedRed: 0.20, green: 0.25, blue: 0.33, alpha: 1)

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        buildWindow()
        captionTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            self?.clearStaleCaptions()
        }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func buildWindow() {
        let size = NSSize(width: 520, height: 314)
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1200, height: 800)
        let origin = NSPoint(x: screen.maxX - size.width - 28, y: screen.maxY - size.height - 48)
        window = NSWindow(
            contentRect: NSRect(origin: origin, size: size),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        window.level = .floating
        window.isOpaque = false
        window.backgroundColor = bg
        window.hasShadow = true
        window.isMovableByWindowBackground = true
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.contentView?.wantsLayer = true
        window.contentView?.layer?.cornerRadius = 14
        window.contentView?.layer?.masksToBounds = true

        guard let view = window.contentView else { return }
        view.addSubview(label("LIVE TRANSLATOR", frame: NSRect(x: 16, y: 277, width: 210, height: 24), size: 15, color: fg, bold: true))
        view.addSubview(label("CHARON", frame: NSRect(x: 170, y: 280, width: 80, height: 18), size: 9, color: muted, bold: true))

        let close = button("×", frame: NSRect(x: 480, y: 274, width: 28, height: 28), color: .clear, action: #selector(closeApp))
        close.attributedTitle = NSAttributedString(
            string: "×",
            attributes: [.foregroundColor: fg, .font: NSFont.systemFont(ofSize: 20, weight: .bold)]
        )
        view.addSubview(close)

        statusDot = label("●", frame: NSRect(x: 16, y: 248, width: 15, height: 18), size: 10, color: muted, bold: false)
        statusLabel = label("TAYYOR", frame: NSRect(x: 34, y: 248, width: 300, height: 18), size: 10, color: muted, bold: true)
        view.addSubview(statusDot)
        view.addSubview(statusLabel)

        view.addSubview(label("YO‘NALISH", frame: NSRect(x: 16, y: 216, width: 90, height: 16), size: 9, color: muted, bold: true))
        directionPopup = NSPopUpButton(frame: NSRect(x: 105, y: 207, width: 300, height: 28), pullsDown: false)
        directionPopup.addItems(withTitles: directions.map { $0.title })
        directionPopup.target = self
        directionPopup.action = #selector(directionChanged)
        directionPopup.appearance = NSAppearance(named: .darkAqua)
        view.addSubview(directionPopup)

        let separator = NSBox(frame: NSRect(x: 15, y: 198, width: 490, height: 1))
        separator.boxType = .separator
        view.addSubview(separator)

        sourceLanguageLabel = label("ORIGINAL  ·  EN", frame: NSRect(x: 16, y: 175, width: 300, height: 16), size: 9, color: muted, bold: true)
        sourceSubtitle = label("Gap kutilmoqda…", frame: NSRect(x: 16, y: 131, width: 488, height: 42), size: 12, color: muted, bold: true, lines: 2)
        view.addSubview(sourceLanguageLabel)
        view.addSubview(sourceSubtitle)

        targetLanguageLabel = label("O‘ZBEKCHA", frame: NSRect(x: 16, y: 110, width: 160, height: 16), size: 9, color: green, bold: true)
        view.addSubview(targetLanguageLabel)
        targetSubtitle = label("Tarjima shu yerda chiqadi…", frame: NSRect(x: 16, y: 66, width: 488, height: 42), size: 12, color: muted, bold: true, lines: 2)
        view.addSubview(targetSubtitle)

        startButton = button("▶  BOSHLASH", frame: NSRect(x: 15, y: 14, width: 235, height: 34), color: green, action: #selector(startTranslator))
        stopButton = button("■  TO‘XTATISH", frame: NSRect(x: 270, y: 14, width: 235, height: 34), color: disabled, action: #selector(stopTranslator))
        view.addSubview(startButton)
        view.addSubview(stopButton)
        setControls(start: true, stop: false)
    }

    private func label(_ text: String, frame: NSRect, size: CGFloat, color: NSColor, bold: Bool, lines: Int = 1) -> NSTextField {
        let field = NSTextField(labelWithString: text)
        field.frame = frame
        field.textColor = color
        field.font = NSFont.systemFont(ofSize: size, weight: bold ? .bold : .regular)
        field.maximumNumberOfLines = lines
        field.lineBreakMode = .byWordWrapping
        field.cell?.wraps = lines > 1
        field.cell?.isScrollable = false
        return field
    }

    private func button(_ title: String, frame: NSRect, color: NSColor, action: Selector) -> NSButton {
        let control = NSButton(frame: frame)
        control.title = title
        control.target = self
        control.action = action
        control.isBordered = false
        control.wantsLayer = true
        control.layer?.backgroundColor = color.cgColor
        control.layer?.cornerRadius = 7
        control.attributedTitle = NSAttributedString(
            string: title,
            attributes: [.foregroundColor: NSColor.white, .font: NSFont.systemFont(ofSize: 10, weight: .bold)]
        )
        return control
    }

    private func setControls(start: Bool, stop: Bool) {
        startButton.isEnabled = start
        stopButton.isEnabled = stop
        startButton.layer?.backgroundColor = (start ? green : disabled).cgColor
        stopButton.layer?.backgroundColor = (stop ? red : disabled).cgColor
        let startColor = start ? NSColor.white : muted
        let stopColor = stop ? NSColor.white : muted
        startButton.attributedTitle = NSAttributedString(string: "▶  BOSHLASH", attributes: [.foregroundColor: startColor, .font: NSFont.systemFont(ofSize: 10, weight: .bold)])
        stopButton.attributedTitle = NSAttributedString(string: "■  TO‘XTATISH", attributes: [.foregroundColor: stopColor, .font: NSFont.systemFont(ofSize: 10, weight: .bold)])
        directionPopup.isEnabled = start
    }

    private func setStatus(_ text: String, color: NSColor) {
        statusDot.stringValue = "●"
        statusDot.textColor = color
        statusLabel.stringValue = text
        statusLabel.textColor = color
    }

    @objc private func startTranslator() {
        guard process == nil else { return }
        setStatus("ULANMOQDA…", color: amber)
        setControls(start: false, stop: true)

        let task = Process()
        let output = Pipe()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/arch")
        task.arguments = [
            "-arm64", "\(projectPath)/.venv/bin/python", "-u",
            "\(projectPath)/translator.py", "--voice", "Charon",
            "--target-language", selectedDirection.target,
        ]
        task.currentDirectoryURL = URL(fileURLWithPath: projectPath)
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        task.environment = environment
        task.standardOutput = output
        task.standardError = output
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let chunk = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.consumeOutput(chunk) }
        }
        task.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async { self?.processFinished(code: finished.terminationStatus) }
        }
        do {
            try task.run()
            process = task
            pipe = output
        } catch {
            process = nil
            pipe = nil
            setStatus("XATO — ISHGA TUSHMADI", color: red)
            setControls(start: true, stop: false)
        }
    }

    @objc private func stopTranslator() {
        guard let task = process, task.isRunning else { return }
        setStatus("TO‘XTATILMOQDA…", color: amber)
        setControls(start: false, stop: false)
        task.interrupt()
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak task] in
            if task?.isRunning == true { task?.terminate() }
        }
    }

    private func consumeOutput(_ chunk: String) {
        outputBuffer.append(chunk)
        while let newline = outputBuffer.firstIndex(of: "\n") {
            let line = String(outputBuffer[..<newline]).trimmingCharacters(in: .whitespacesAndNewlines)
            outputBuffer.removeSubrange(...newline)
            handleLine(line)
        }
    }

    private func handleLine(_ line: String) {
        guard !line.isEmpty else { return }
        if line.contains("Ulandi.") {
            setStatus("TARJIMA ISHLAYAPTI", color: green)
            return
        }
        if line.contains("qayta ulanadi") {
            setStatus("QAYTA ULANMOQDA…", color: amber)
            return
        }
        guard let divider = line.range(of: " › ") else { return }
        let language = String(line[..<divider.lowerBound])
        let text = String(line[divider.upperBound...])
        guard !text.isEmpty else { return }
        if language.lowercased().hasPrefix(selectedDirection.target.lowercased()) {
            updateTarget(text)
        } else if language.count <= 12 {
            updateSource(language: language, text: text)
        }
    }

    private func appended(_ current: String, chunk: String, last: Date) -> (String, Bool) {
        let newTurn = current.isEmpty || Date().timeIntervalSince(last) > 2 || current.hasSuffix(".") || current.hasSuffix("?") || current.hasSuffix("!")
        var result = newTurn ? chunk : "\(current) \(chunk)"
        if result.count > 140 { result = "…" + String(result.suffix(139)) }
        return (result, newTurn)
    }

    private func updateSource(language: String, text: String) {
        let (caption, newTurn) = appended(sourceCaption, chunk: text, last: lastSourceAt)
        sourceCaption = caption
        lastSourceAt = Date()
        sourceLanguageLabel.stringValue = "ORIGINAL  ·  \(language.uppercased())"
        sourceSubtitle.stringValue = caption
        sourceSubtitle.textColor = fg
        if newTurn {
            targetCaption = ""
            targetSubtitle.stringValue = "Tarjima qilinmoqda…"
            targetSubtitle.textColor = muted
        }
    }

    private func updateTarget(_ text: String) {
        let (caption, _) = appended(targetCaption, chunk: text, last: lastTargetAt)
        targetCaption = caption
        lastTargetAt = Date()
        targetSubtitle.stringValue = caption
        targetSubtitle.textColor = NSColor(calibratedRed: 0.86, green: 0.99, blue: 0.91, alpha: 1)
    }

    private func clearStaleCaptions() {
        let latest = max(lastSourceAt, lastTargetAt)
        guard latest != Date.distantPast, Date().timeIntervalSince(latest) >= 12 else { return }
        sourceCaption = ""
        targetCaption = ""
        lastSourceAt = .distantPast
        lastTargetAt = .distantPast
        sourceLanguageLabel.stringValue = "ORIGINAL  ·  \(selectedDirection.source)"
        sourceSubtitle.stringValue = "Gap kutilmoqda…"
        sourceSubtitle.textColor = muted
        targetSubtitle.stringValue = "Tarjima shu yerda chiqadi…"
        targetSubtitle.textColor = muted
    }

    private var selectedDirection: (title: String, source: String, target: String, targetName: String) {
        let index = max(0, min(directionPopup.indexOfSelectedItem, directions.count - 1))
        return directions[index]
    }

    @objc private func directionChanged() {
        sourceCaption = ""
        targetCaption = ""
        lastSourceAt = .distantPast
        lastTargetAt = .distantPast
        sourceLanguageLabel.stringValue = "ORIGINAL  ·  \(selectedDirection.source)"
        targetLanguageLabel.stringValue = selectedDirection.targetName
        sourceSubtitle.stringValue = "Gap kutilmoqda…"
        sourceSubtitle.textColor = muted
        targetSubtitle.stringValue = "Tarjima shu yerda chiqadi…"
        targetSubtitle.textColor = muted
    }

    private func processFinished(code: Int32) {
        pipe?.fileHandleForReading.readabilityHandler = nil
        process = nil
        pipe = nil
        setControls(start: true, stop: false)
        if closing {
            NSApp.terminate(nil)
        } else if code == 0 || code == 2 {
            setStatus(code == 0 ? "TO‘XTADI" : "XATO — QAYTA BOSING", color: code == 0 ? muted : red)
        } else {
            setStatus("XATO — QAYTA BOSING", color: red)
        }
    }

    @objc private func closeApp() {
        closing = true
        captionTimer?.invalidate()
        if process?.isRunning == true {
            stopTranslator()
        } else {
            NSApp.terminate(nil)
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        captionTimer?.invalidate()
        pipe?.fileHandleForReading.readabilityHandler = nil
        if let task = process, task.isRunning {
            kill(task.processIdentifier, SIGTERM)
        }
    }
}

let application = NSApplication.shared
private let delegate = AppDelegate()
application.delegate = delegate
application.run()
