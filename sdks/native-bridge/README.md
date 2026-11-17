# Native NFC bridge: the only code that must run on the phone (reference source, NOT compiled, NOT run)

**Status: reference source written from the platform vendors' documented APIs. It has never been compiled and never touched a passport. Treat it as E1 (code referenced), not tested. It needs a developer to build it, and a device matrix and real documents before anyone calls it working.**

## What it is for

The platform reads a passport chip itself (CR-108): Basic Access Control, secure messaging, file reading and Active Authentication all run on our server. The phone is only a wire. It has to do one thing a browser cannot: send a command to the chip over NFC and bring the answer back. That is `transceive`.

```
phone / app shell           our cell (keeps nothing between steps)
 transceive(bytes) <------  "send this command" + a sealed token (POST .../chip-link)
 chip's answer + token ---->  next command + a fresh token (POST .../chip-link/step) ... until the read ends
                             then POST .../chip-link/finish {token} judges it
```

The TypeScript side of the loop is `readChipThroughRelay` in `sdks/typescript/src/chip-relay.ts` (tested). It takes a `Transceive = (apdu: Uint8Array) => Promise<Uint8Array>`. The code below is what supplies it.

## The contract the bridge must keep

1. Send exactly the bytes it is given, unchanged, to the chip. Return the chip's response **including the two status bytes** (SW1 SW2) at the end.
2. Never look inside a command or an answer. Never keep one. Never log one.
3. Keep the chip connected for the whole read (about 20 to 60 commands without the photo, more with it). Raise the transceive timeout (a chip can take seconds on BAC).
4. If the chip is lost, throw. The TypeScript loop stops (nothing is left running on the platform and the token expires by itself) and tells the person to hold the phone still and try again.
5. The person types or scans the document number, date of birth and expiry date; the app passes them to `readChipThroughRelay`. The bridge itself never sees them.

## Android (Kotlin): `IsoDep`

Passports are ISO/IEC 14443-4 (Type A or Type B), which Android exposes as `IsoDep`.

```kotlin
// Enable reader mode so the system does not hand the tag to another app, and skip the NDEF check (a passport has none).
nfcAdapter.enableReaderMode(
    activity,
    { tag: Tag -> bridge = ChipBridge(IsoDep.get(tag)) /* then start the read */ },
    NfcAdapter.FLAG_READER_NFC_A or NfcAdapter.FLAG_READER_NFC_B or NfcAdapter.FLAG_READER_SKIP_NDEF_CHECK,
    null,
)

class ChipBridge(private val iso: IsoDep) {
    init { iso.connect(); iso.timeout = 15_000 }
    /** The whole bridge: bytes in, bytes out, status bytes included. */
    fun transceive(apdu: ByteArray): ByteArray = iso.transceive(apdu)
    fun close() = runCatching { iso.close() }
}
```

Manifest: `<uses-permission android:name="android.permission.NFC" />` and `<uses-feature android:name="android.hardware.nfc" android:required="false" />` (a phone without NFC must fall back to another route, not fail to install).

## iOS (Swift): `NFCTagReaderSession` (CoreNFC)

```swift
// Info.plist: NFCReaderUsageDescription, and ISO7816 select identifiers containing the LDS application:
//   com.apple.developer.nfc.readersession.iso7816.select-identifiers = [ "A0000002471001" ]
// Entitlement: Near Field Communication Tag Reading (the app's capabilities).
let session = NFCTagReaderSession(pollingOption: [.iso14443], delegate: self)
session?.alertMessage = "Hold the top of your phone against the passport."
session?.begin()

func tagReaderSession(_ s: NFCTagReaderSession, didDetect tags: [NFCTag]) {
    guard case .iso7816(let tag) = tags.first else { return }
    s.connect(to: tags.first!) { error in /* on success, start the read with bridge = ChipBridge(tag) */ }
}

final class ChipBridge {
    let tag: NFCISO7816Tag
    init(_ tag: NFCISO7816Tag) { self.tag = tag }
    /// Bytes in, bytes out, with SW1 SW2 appended so the answer is the same shape Android returns.
    func transceive(_ apdu: Data) async throws -> Data {
        guard let command = NFCISO7816APDU(data: apdu) else { throw BridgeError.malformed }
        let (data, sw1, sw2) = try await tag.sendCommand(apdu: command)
        return data + Data([sw1, sw2])
    }
}
```

## From the app shell to the TypeScript loop

An app shell (Capacitor, React Native or a plain native app with a web view) exposes the native `transceive` to JavaScript as base64 in and out and wraps it:

```ts
import { readChipThroughRelay, finishChipRead, toBase64, fromBase64 } from "@zoiko/credentials";

const transceive = async (apdu: Uint8Array) => fromBase64(await Native.transceive(toBase64(apdu))); // Native = the shell's plugin
const read = await readChipThroughRelay(cfg, verificationId, { document_number, date_of_birth, document_valid_to }, transceive);
const result = await finishChipRead(cfg, verificationId, read.token); // judged now; the session is decided once
```

## What is not here, and why

- No project files, no build configuration, no store listing: they need an SDK installed and a signing identity.
- Nothing about PACE: it runs on the server (CR-108), so the bridge is the same for a Basic Access Control chip and a PACE chip. The app may also take the Card Access Number (printed on the document) and send it as `can` instead of the three details: the server then runs PACE with it. A chip that offers only a PACE variant the server does not implement answers with that variant named; the app should then offer another route (the alternatives route lists them).
- No camera. A selfie is separate and only matters once an engine has been evaluated and approved for the country.

## Before this is called working

A developer builds both shells; it is run against real passports from several countries on several phones (Android and iOS, a range of models and OS versions); the failures are recorded in the device matrix. WF01 requires that matrix (T04) and the independent mobile review (WF01-010) before a market is enabled.
