// swift-tools-version:5.9
// PAi Health phone companion core for iPhone: protocol v1 envelope, pairing, sync client, dose rules.
// Mirrors engine/companion/protocol.py and android/.../core/*.java; held to them by
// Tests/CompanionCoreTests/Resources/vectors.json (tools/gen_companion_vectors.py).
import PackageDescription

let package = Package(
    name: "CompanionCore",
    platforms: [.iOS(.v16), .macOS(.v13)],
    products: [
        .library(name: "CompanionCore", targets: ["CompanionCore"]),
        .executable(name: "companion-interop", targets: ["CompanionInterop"]),
    ],
    targets: [
        .target(name: "CompanionCore"),
        .executableTarget(name: "CompanionInterop", dependencies: ["CompanionCore"]),
        .testTarget(name: "CompanionCoreTests", dependencies: ["CompanionCore"],
                    resources: [.copy("Resources/vectors.json")]),
    ]
)
