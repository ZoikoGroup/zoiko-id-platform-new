// swift-tools-version: 5.9
// NOT BUILT. A Swift package holding the chip bridge; an app target adds it and the NFC capability (see the README).
import PackageDescription

let package = Package(
    name: "ChipBridge",
    platforms: [.iOS(.v15)],
    products: [.library(name: "ChipBridge", targets: ["ChipBridge"])],
    targets: [.target(name: "ChipBridge", path: "Sources/ChipBridge")]
)
