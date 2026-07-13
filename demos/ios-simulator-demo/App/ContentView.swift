import SwiftUI

struct ContentView: View {
    @State private var probeState = "Idle"
    @State private var userInput = ""
    @State private var echoedText = "No input yet"

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                Text("CodeAutonomy iOS Harness Demo")
                    .font(.title)
                    .bold()
                    .accessibilityIdentifier("demo_title")

                Text("Simulator validation loop ready")
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .accessibilityIdentifier("demo_subtitle")

                Text("Probe state: \(probeState)")
                    .font(.headline)
                    .accessibilityIdentifier("probe_state_label")

                TextField("Type something", text: $userInput)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityIdentifier("demo_text_field")

                Button("Echo Input") {
                    echoedText = userInput.isEmpty ? "Empty input" : userInput
                    print("[AutoMindIOSDemo] echo_input=\(echoedText)")
                }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("echo_button")

                Text("Echo: \(echoedText)")
                    .accessibilityIdentifier("echo_result_label")

                Button("Run Probe") {
                    probeState = "Completed"
                    print("[AutoMindIOSDemo] probe_button_tapped")
                }
                .buttonStyle(.borderedProminent)
                .accessibilityIdentifier("probe_button")

                Button("Trigger Crash") {
                    fatalError("AutoMindIOSDemo crash probe")
                }
                .buttonStyle(.bordered)
                .tint(.red)
                .accessibilityIdentifier("crash_button")

                ForEach(1...30, id: \.self) { index in
                    Text("Scrollable item \(index)")
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 4)
                        .accessibilityIdentifier("scroll_item_\(index)")
                }
            }
            .padding(24)
        }
    }
}

#Preview {
    ContentView()
}
