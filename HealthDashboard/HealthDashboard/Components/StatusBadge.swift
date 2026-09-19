import SwiftUI

struct StatusBadge: View {
    let status: RAGStatus?

    var body: some View {
        Text(label)
            .font(.caption2)
            .fontWeight(.semibold)
            .textCase(.uppercase)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color.opacity(0.15))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }

    private var label: String {
        switch status {
        case .green: return "Normal"
        case .amber: return "Watch"
        case .red: return "Flag"
        case .unknown, .none: return "N/A"
        }
    }

    private var color: Color {
        switch status {
        case .green: return .green
        case .amber: return .orange
        case .red: return .red
        case .unknown, .none: return .secondary
        }
    }
}

struct StatusDot: View {
    let status: RAGStatus?

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 8, height: 8)
    }

    private var color: Color {
        switch status {
        case .green: return .green
        case .amber: return .orange
        case .red: return .red
        case .unknown, .none: return .secondary
        }
    }
}
