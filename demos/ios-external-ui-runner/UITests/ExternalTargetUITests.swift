import XCTest

final class ExternalTargetUITests: XCTestCase {
    override func setUp() {
        super.setUp()
        continueAfterFailure = false
    }

    func testExternalTargetLaunches() throws {
        let bundleId = ProcessInfo.processInfo.environment["AUTOMIND_TARGET_BUNDLE_ID"] ?? "ai.openclaw.automind.external.demo"
        let app = XCUIApplication(bundleIdentifier: bundleId)
        app.launch()
        XCTAssertTrue(
            app.state == .runningForeground || app.state == .runningBackground,
            "Expected target app \(bundleId) to launch"
        )
    }
}
