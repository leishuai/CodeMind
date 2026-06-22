import XCTest

final class AutoMindIOSDemoUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testProbeButtonChangesStateToCompleted() throws {
        let app = XCUIApplication()
        app.launch()

        let stateLabel = app.staticTexts["probe_state_label"]
        XCTAssertTrue(stateLabel.waitForExistence(timeout: 5))
        XCTAssertEqual(stateLabel.label, "Probe state: Idle")

        let probeButton = app.buttons["probe_button"]
        XCTAssertTrue(probeButton.waitForExistence(timeout: 5))
        probeButton.tap()

        XCTAssertEqual(stateLabel.label, "Probe state: Completed")
    }

    func testInputEchoFlow() throws {
        let app = XCUIApplication()
        app.launch()

        let textField = app.textFields["demo_text_field"]
        XCTAssertTrue(textField.waitForExistence(timeout: 5))
        textField.tap()
        textField.typeText("hello simulator")

        let echoButton = app.buttons["echo_button"]
        XCTAssertTrue(echoButton.waitForExistence(timeout: 5))
        echoButton.tap()

        let resultLabel = app.staticTexts["echo_result_label"]
        XCTAssertTrue(resultLabel.waitForExistence(timeout: 5))
        XCTAssertEqual(resultLabel.label, "Echo: hello simulator")
    }

    func testScrollToBottomItem() throws {
        let app = XCUIApplication()
        app.launch()

        let target = app.staticTexts["scroll_item_30"]
        for _ in 0..<8 where !target.exists {
            app.swipeUp()
        }

        XCTAssertTrue(target.waitForExistence(timeout: 2))
    }
}
