import SwiftUI

@main
struct AutoMindIOSDemoApp: App {
    init() {
        if CommandLine.arguments.contains("--crash-on-launch") {
            fatalError("AutoMindIOSDemo crash probe")
        }
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
