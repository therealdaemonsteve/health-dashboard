import Foundation

enum Formatters {
    // MARK: - Date Formatters

    /// ISO date formatter for `yyyy-MM-dd` strings. Thread-safe via nonisolated(unsafe) + immutability.
    nonisolated(unsafe) static let isoDate: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    /// Parse a `yyyy-MM-dd` string to Date.
    static func parseDate(_ string: String) -> Date? {
        isoDate.date(from: String(string.prefix(10)))
    }

    /// Format a Date to `yyyy-MM-dd` string.
    static func dateString(from date: Date) -> String {
        isoDate.string(from: date)
    }

    /// Today as `yyyy-MM-dd`.
    static var today: String {
        dateString(from: Date())
    }

    // MARK: - Value Formatting

    /// Smart-format a numeric value based on magnitude.
    /// - >= 100: no decimals (e.g. "1523")
    /// - >= 10: one decimal (e.g. "78.3")
    /// - >= 1: one decimal (e.g. "4.7")
    /// - < 1: two or three decimals (e.g. "0.85", "0.003")
    static func value(_ v: Double) -> String {
        if v.isNaN || v.isInfinite { return "-" }
        let abs = Swift.abs(v)
        if abs >= 100 { return String(format: "%.0f", v) }
        if abs >= 1 { return String(format: "%.1f", v) }
        if abs >= 0.01 { return String(format: "%.2f", v) }
        return String(format: "%.3f", v)
    }

    /// Format an optional value, returning "-" for nil.
    static func value(_ v: Double?) -> String {
        guard let v else { return "-" }
        return value(v)
    }

    /// Format a value with its unit, e.g. "78.3 kg".
    static func valueWithUnit(_ v: Double, unit: String?) -> String {
        if let unit, !unit.isEmpty {
            return "\(value(v)) \(unit)"
        }
        return value(v)
    }

    /// Format a percentage change with sign, e.g. "+12%" or "-5%".
    static func pctChange(_ pct: Double) -> String {
        String(format: "%+.0f%%", pct)
    }
}
