// NOT COMPILED, NOT RUN: written from Apple's documented CoreNFC API (sdks/native-bridge/README.md). It has never touched a passport.
//
// The whole phone side of the platform's chip read: bytes in, bytes out, status bytes appended so the answer has the same shape Android returns. It sends exactly the
// bytes it is given, never looks inside them, never keeps or logs one, and says only "device" when anything fails.

import CoreNFC
import Foundation
import WebKit

public enum ChipBridgeError: Error { case malformed, unavailable }

public final class ChipBridge {
    private let tag: NFCISO7816Tag
    public init(_ tag: NFCISO7816Tag) { self.tag = tag }

    /// One command APDU in, the chip's response with SW1 SW2 appended out.
    public func transceive(_ apdu: Data) async throws -> Data {
        guard apdu.count >= 4, apdu.count <= 4096, let command = NFCISO7816APDU(data: apdu) else { throw ChipBridgeError.malformed }
        let (data, sw1, sw2) = try await tag.sendCommand(apdu: command)
        return data + Data([sw1, sw2])
    }
}

/// Finds the chip and answers `window.webkit.messageHandlers.zoikoChip.postMessage(base64)` with the chip's answer as base64 (or the text `ERROR:device`).
/// The page then calls the TypeScript `transceiveFromNative({ transceive: (b) => window.webkit.messageHandlers.zoikoChip.postMessage(b) })`.
/// This class never sees the document details or any chip data.
public final class NfcChipReader: NSObject, NFCTagReaderSessionDelegate, WKScriptMessageHandlerWithReply {
    private var session: NFCTagReaderSession?
    private var bridge: ChipBridge?
    private let onChip: () -> Void
    private let onLost: () -> Void

    public init(onChip: @escaping () -> Void, onLost: @escaping () -> Void) {
        self.onChip = onChip
        self.onLost = onLost
    }

    public static var isAvailable: Bool { NFCTagReaderSession.readingAvailable }

    /// Register on the web view's configuration before it loads the page.
    public func attach(to configuration: WKWebViewConfiguration) {
        configuration.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "zoikoChip")
    }

    public func begin() {
        // Needs the Near Field Communication Tag Reading capability, NFCReaderUsageDescription, and the LDS application identifier A0000002471001 in the
        // com.apple.developer.nfc.readersession.iso7816.select-identifiers entry of Info.plist.
        session = NFCTagReaderSession(pollingOption: [.iso14443], delegate: self)
        session?.alertMessage = "Hold the top of your phone against the passport."
        session?.begin()
    }

    public func end() {
        session?.invalidate()
        session = nil
        bridge = nil
    }

    // MARK: NFCTagReaderSessionDelegate
    public func tagReaderSessionDidBecomeActive(_ session: NFCTagReaderSession) {}

    public func tagReaderSession(_ session: NFCTagReaderSession, didInvalidateWithError error: Error) {
        bridge = nil
        DispatchQueue.main.async(execute: onLost)
    }

    public func tagReaderSession(_ session: NFCTagReaderSession, didDetect tags: [NFCTag]) {
        guard let first = tags.first, case .iso7816(let iso) = first else {
            session.invalidate(errorMessage: "This is not a passport chip.")
            return
        }
        session.connect(to: first) { [weak self] error in
            guard let self else { return }
            if error == nil {
                self.bridge = ChipBridge(iso)
                DispatchQueue.main.async(execute: self.onChip)
            } else {
                session.invalidate(errorMessage: "Could not read the chip. Hold still and try again.")
            }
        }
    }

    // MARK: WKScriptMessageHandlerWithReply
    public func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage, replyHandler: @escaping (Any?, String?) -> Void) {
        guard let text = message.body as? String, let command = Data(base64Encoded: text), let bridge else {
            replyHandler("ERROR:device", nil)
            return
        }
        Task {
            do { replyHandler(try await bridge.transceive(command).base64EncodedString(), nil) }
            catch { replyHandler("ERROR:device", nil) }
        }
    }
}
